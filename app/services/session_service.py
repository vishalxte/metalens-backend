"""
Login session lifecycle service — the only writer of `user_sessions`.

Same construction as AuditService (module-level singleton, own
short-lived SessionLocal per operation, mirrors CacheService), for the
same reasons: session bookkeeping is infrastructure, not business logic,
and must not be able to poison a caller's transaction.

──────────────────────────────────────────────────────────────────────
FAIL-OPEN vs FAIL-CLOSED — the single most important decision here
──────────────────────────────────────────────────────────────────────
This service sits on the authentication path, so its failure mode has
to be chosen deliberately rather than inherited:

  is_revoked() FAILS OPEN.
      If the session table is unreachable, or the row is simply absent,
      the request is allowed through exactly as it was before this
      feature existed. Fail-closed would mean one DB hiccup logs out
      every user in the deployment — turning a monitoring problem into
      a total outage. Only an explicitly read, non-NULL `ended_at` ever
      rejects a request.

  start() / touch() / end() NEVER RAISE.
      A login must not fail because the session row could not be
      written; a logout must not 500 because the row was already gone.
      Failures are reported to the application logger only, matching
      the audit framework's rule.

The security trade-off is accepted knowingly: revocation is
best-effort, bounded by the token's own `exp`
(ACCESS_TOKEN_EXPIRE_MINUTES, currently 60). A revoked session is
rejected as long as the DB is readable, and in the worst case the token
still dies on its own within the hour. That is the standard posture for
stateless-JWT-plus-revocation-list designs.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.config import settings
from app.core.logging import logger
from app.core.request_context import get_audit_context
from app.database.session import SessionLocal
from app.models.user_session import (
    UserSession,
    SessionEndReason,
    _as_aware
)
from app.repositories.user_session_repository import UserSessionRepository
from app.core.user_agent import parse_user_agent


_MAX_LEN = {
    "session_id": 64,
    "user_email": 320,
    "role": 32,
    "ip_address": 64,
    "user_agent": 512,
    "browser": 64,
    "platform": 64,
    "device_name": 128
}


def _truncate(value: Optional[str], field: str) -> Optional[str]:
    if value is None:
        return None

    limit = _MAX_LEN.get(field)
    text = str(value)

    return text[:limit] if limit else text


class SessionService:

    # ─────────────────────────────────────────────────────────────
    # Lifecycle: open
    # ─────────────────────────────────────────────────────────────
    def start(
        self,
        session_id: str,
        user_id: Optional[int],
        user_email: Optional[str],
        role: Optional[str],
        customer_id: Optional[int],
        expires_at: Optional[datetime] = None
    ) -> None:
        """
        Opens the session row for a freshly issued token. Called from
        AuthService.login() with the same `jti` that went into the JWT.

        IP and user agent come from the request context (captured by
        RequestLoggingMiddleware), not from arguments — same principle
        as AuditService: the caller describes only what happened.
        """
        try:
            if not settings.SESSION_TRACKING_ENABLED:
                return

            if not session_id:
                return

            context = get_audit_context()

            if expires_at is None:
                expires_at = datetime.now(timezone.utc) + timedelta(
                    minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
                )

            # Parsed ONCE, here, rather than on every read of the
            # session list. The raw user_agent is stored alongside, so
            # these stay recomputable.
            browser, platform, device_name = parse_user_agent(
                context["user_agent"]
            )

            now = datetime.now(timezone.utc)

            entry = UserSession(
                session_id=_truncate(session_id, "session_id"),
                user_id=user_id,
                user_email=_truncate(user_email, "user_email"),
                role=_truncate(role, "role"),
                customer_id=customer_id,
                login_at=now,
                last_seen_at=now,
                expires_at=expires_at,
                ip_address=_truncate(context["ip_address"], "ip_address"),
                user_agent=_truncate(context["user_agent"], "user_agent"),
                browser=_truncate(browser, "browser"),
                platform=_truncate(platform, "platform"),
                device_name=_truncate(device_name, "device_name")
            )

            db = SessionLocal()
            try:
                UserSessionRepository(db).create(entry)
                logger.info(
                    f"Session started — session_id={session_id}, email={user_email!r}, role={role!r}",
                    extra={"event": "session_started", "session_id": session_id}
                )
            finally:
                db.close()

        except Exception:
            self._report_failure("start", session_id)

    # ─────────────────────────────────────────────────────────────
    # Lifecycle: per-request check + heartbeat
    # ─────────────────────────────────────────────────────────────
    def check_and_touch(
        self,
        session_id: Optional[str]
    ) -> Optional[str]:
        """
        Runs once per authenticated request, from get_current_user().

        Returns the end_reason if the session has been closed and the
        request must be rejected, or None if it should proceed.

        Returning None (proceed) is the answer for every ambiguous case:
        session tracking disabled, no `jti` in the token, no row found,
        or the lookup itself failed. See the fail-open note in the
        module docstring.

        The last_seen_at write is throttled to at most once per
        SESSION_TOUCH_INTERVAL_SECONDS. Without that, every single read
        request in the application would become a write — the usual way
        naive session tracking wrecks database performance.
        """
        try:
            if not settings.SESSION_TRACKING_ENABLED:
                return None

            if not settings.SESSION_REVOCATION_CHECK_ENABLED:
                return None

            if not session_id:
                # Token predates this feature (no `jti`). Allowed, so
                # existing logged-in users are not kicked out by the
                # deployment itself.
                return None

            db = SessionLocal()
            try:
                repo = UserSessionRepository(db)
                user_session = repo.get_by_session_id(session_id)

                if user_session is None:
                    # A valid token with no session row — e.g. issued
                    # before this table existed. Allowed.
                    return None

                if user_session.ended_at is not None:
                    logger.warning(
                        f"Rejected request on a closed session — session_id={session_id}, "
                        f"reason={user_session.ended_reason}",
                        extra={
                            "event": "session_rejected",
                            "session_id": session_id,
                            "ended_reason": user_session.ended_reason
                        }
                    )
                    return user_session.ended_reason or SessionEndReason.REVOKED

                now = datetime.now(timezone.utc)

                # Throttled heartbeat. _as_aware guards against a row
                # whose offset was lost somewhere upstream (restored
                # dump, bulk import) — without it the subtraction below
                # raises, and since this method fails open that would
                # silently stop enforcing revocation.
                last_seen = _as_aware(user_session.last_seen_at)

                if (
                    last_seen is None
                    or (now - last_seen).total_seconds()
                    >= settings.SESSION_TOUCH_INTERVAL_SECONDS
                ):
                    repo.touch(user_session, now=now)

                return None

            finally:
                db.close()

        except Exception:
            self._report_failure("check_and_touch", session_id)
            # FAIL OPEN — see the module docstring.
            return None

    # ─────────────────────────────────────────────────────────────
    # Lifecycle: close
    # ─────────────────────────────────────────────────────────────
    def end(
        self,
        session_id: Optional[str],
        reason: str = SessionEndReason.LOGOUT,
        revoked_by_user_id: Optional[int] = None
    ) -> bool:
        """Closes one session. Returns whether a row was actually closed."""
        try:
            if not settings.SESSION_TRACKING_ENABLED or not session_id:
                return False

            db = SessionLocal()
            try:
                repo = UserSessionRepository(db)
                user_session = repo.get_by_session_id(session_id)

                if user_session is None or user_session.ended_at is not None:
                    return False

                repo.end(
                    user_session,
                    reason=reason,
                    revoked_by_user_id=revoked_by_user_id
                )

                logger.info(
                    f"Session ended — session_id={session_id}, reason={reason}",
                    extra={
                        "event": "session_ended",
                        "session_id": session_id,
                        "ended_reason": reason
                    }
                )
                return True

            finally:
                db.close()

        except Exception:
            self._report_failure("end", session_id)
            return False

    def end_all_for_user(
        self,
        user_id: int,
        reason: str = SessionEndReason.REVOKED,
        revoked_by_user_id: Optional[int] = None
    ) -> int:
        """
        "Log this person out everywhere." Used by the admin revoke
        action and when an account is deactivated.
        """
        try:
            if not settings.SESSION_TRACKING_ENABLED:
                return 0

            db = SessionLocal()
            try:
                count = UserSessionRepository(db).end_all_for_user(
                    user_id,
                    reason=reason,
                    revoked_by_user_id=revoked_by_user_id
                )

                if count:
                    logger.info(
                        f"Ended {count} session(s) for user_id={user_id} — reason={reason}",
                        extra={
                            "event": "sessions_bulk_ended",
                            "target_user_id": user_id,
                            "ended_reason": reason,
                            "count": count
                        }
                    )

                return count

            finally:
                db.close()

        except Exception:
            self._report_failure("end_all_for_user", str(user_id))
            return 0

    def end_all_for_customer(
        self,
        customer_id: int,
        reason: str = SessionEndReason.ACCOUNT_DISABLED,
        revoked_by_user_id: Optional[int] = None
    ) -> int:
        """
        "Disable this whole company." Called when a Customer is
        deactivated or soft-deleted, since that cascades is_active=False
        onto every user under the tenant.
        """
        try:
            if not settings.SESSION_TRACKING_ENABLED:
                return 0

            db = SessionLocal()
            try:
                count = UserSessionRepository(db).end_all_for_customer(
                    customer_id,
                    reason=reason,
                    revoked_by_user_id=revoked_by_user_id
                )

                if count:
                    logger.info(
                        f"Ended {count} session(s) for customer_id={customer_id} — reason={reason}",
                        extra={
                            "event": "sessions_bulk_ended",
                            "target_customer_id": customer_id,
                            "ended_reason": reason,
                            "count": count
                        }
                    )

                return count

            finally:
                db.close()

        except Exception:
            self._report_failure("end_all_for_customer", str(customer_id))
            return 0

    def expire_stale(self) -> int:
        """
        Stamps ended_at / ended_reason=EXPIRED on sessions whose token
        expiry has passed but which were never explicitly closed — the
        user simply closed the tab or walked away instead of logging out.

        WHY THIS IS NEEDED AT ALL
        ─────────────────────────
        `ended_at` is only written by an explicit action: logout, revoke,
        or account deactivation. A session that just runs out of time has
        no such moment, so without this sweep it keeps ended_at = NULL
        forever even though it is long dead. Correctness was never
        affected — is_active() and the active_only filter both compare
        against expires_at, so a lapsed session has always been excluded
        from "who is online". But the NULL made the table read as though
        every session were still open, and made "ended by logout vs.
        timed out" unanswerable.

        Called on startup and lazily whenever an admin lists sessions
        (see app/api/v1/sessions.py), so the column is accurate whenever
        anyone actually looks at it — without a scheduler or a
        per-request write.

        Never raises: this is bookkeeping, and it must not be able to
        break application startup or an admin screen.
        """
        try:
            if not settings.SESSION_TRACKING_ENABLED:
                return 0

            db = SessionLocal()
            try:
                count = UserSessionRepository(db).expire_stale()

                if count:
                    logger.info(
                        f"Marked {count} lapsed session(s) as EXPIRED",
                        extra={"event": "sessions_expired", "count": count}
                    )

                return count

            finally:
                db.close()

        except Exception:
            self._report_failure("expire_stale", None)
            return 0

    def _report_failure(self, operation: str, session_id: Optional[str]) -> None:
        try:
            logger.error(
                f"SESSION {operation.upper()} FAILED for session_id={session_id!r} — "
                f"the request was NOT affected (fail-open)",
                exc_info=True,
                extra={
                    "event": "session_operation_failed",
                    "operation": operation,
                    "session_id": session_id
                }
            )
        except Exception:
            pass


# Module-level singleton, same pattern as audit_service / cache_service.
session_service = SessionService()
