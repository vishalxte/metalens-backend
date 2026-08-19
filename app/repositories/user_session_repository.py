"""
Data access for user_sessions. Same shape as every other repository in
this project (constructor takes a Session, methods own their commit).

Unlike AuditRepository this table IS mutable — a session legitimately
changes state over its life (last_seen_at, then ended_at/end_reason).
What it never does is lose history: closing a session updates the row,
it never deletes it, so "who was logged in last Tuesday" stays
answerable.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.user_session import UserSession, SessionEndReason


class UserSessionRepository:

    def __init__(
        self,
        db: Session
    ):
        self.db = db

    def create(
        self,
        user_session: UserSession
    ) -> UserSession:

        self.db.add(user_session)

        self.db.commit()

        self.db.refresh(user_session)

        return user_session

    def get_by_session_id(
        self,
        session_id: str
    ) -> Optional[UserSession]:
        """
        The hot path — called once per authenticated request to check
        revocation. Backed by the UNIQUE index on session_id, so it is a
        single index lookup.
        """
        return (
            self.db.query(UserSession)
            .filter(UserSession.session_id == session_id)
            .first()
        )

    def touch(
        self,
        user_session: UserSession,
        now: Optional[datetime] = None
    ) -> None:
        """
        Records that the session is still in use. Callers are expected
        to throttle (see SessionService) — this method itself always
        writes.
        """
        user_session.last_seen_at = now or datetime.now(timezone.utc)
        self.db.commit()

    def end(
        self,
        user_session: UserSession,
        reason: str,
        revoked_by_user_id: Optional[int] = None,
        now: Optional[datetime] = None
    ) -> UserSession:
        """
        Closes a session. Idempotent: an already-closed session keeps its
        ORIGINAL ended_at and reason, so a double logout (or a logout
        arriving after an admin already revoked it) can never rewrite
        history or hide the real cause.
        """
        if user_session.ended_at is not None:
            return user_session

        user_session.ended_at = now or datetime.now(timezone.utc)
        user_session.ended_reason = reason
        user_session.revoked_by_user_id = revoked_by_user_id

        self.db.commit()
        self.db.refresh(user_session)

        return user_session

    def end_all_for_user(
        self,
        user_id: int,
        reason: str = SessionEndReason.REVOKED,
        revoked_by_user_id: Optional[int] = None,
        now: Optional[datetime] = None
    ) -> int:
        """
        Terminates every live session belonging to one user — "log this
        person out of everywhere". Used when an account is deactivated
        or deleted, and by the admin revoke-all action.
        """
        now = now or datetime.now(timezone.utc)

        updated = (
            self.db.query(UserSession)
            .filter(
                UserSession.user_id == user_id,
                UserSession.ended_at.is_(None)
            )
            .update(
                {
                    "ended_at": now,
                    "ended_reason": reason,
                    "revoked_by_user_id": revoked_by_user_id
                },
                synchronize_session=False
            )
        )

        self.db.commit()

        return updated

    def end_all_for_customer(
        self,
        customer_id: int,
        reason: str = SessionEndReason.ACCOUNT_DISABLED,
        revoked_by_user_id: Optional[int] = None,
        now: Optional[datetime] = None
    ) -> int:
        """
        Terminates every live session across an entire tenant.

        Needed because deactivating a Customer cascades is_active=False
        onto every user under it (see CustomerService.update ->
        UserRepository.set_active_for_customer). Without this, all of
        those users keep valid tokens until expiry — the tenant looks
        disabled in the UI while its staff carry on working.

        Deliberately ONE bulk UPDATE rather than a loop of
        end_all_for_user(): a company can have many users, and the
        cascade already runs inside a request the admin is waiting on.
        """
        now = now or datetime.now(timezone.utc)

        updated = (
            self.db.query(UserSession)
            .filter(
                UserSession.customer_id == customer_id,
                UserSession.ended_at.is_(None)
            )
            .update(
                {
                    "ended_at": now,
                    "ended_reason": reason,
                    "revoked_by_user_id": revoked_by_user_id
                },
                synchronize_session=False
            )
        )

        self.db.commit()

        return updated

    def _apply_filters(
        self,
        query,
        user_id: Optional[int] = None,
        user_email: Optional[str] = None,
        role: Optional[str] = None,
        customer_id: Optional[int] = None,
        active_only: bool = False,
        ended_reason: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        now: Optional[datetime] = None
    ):
        if user_id is not None:
            query = query.filter(UserSession.user_id == user_id)

        if user_email:
            query = query.filter(UserSession.user_email == user_email)

        if role:
            query = query.filter(UserSession.role == role)

        if customer_id is not None:
            query = query.filter(UserSession.customer_id == customer_id)

        if active_only:
            # Matches the partial index on the model exactly.
            query = query.filter(
                UserSession.ended_at.is_(None),
                UserSession.expires_at > (now or datetime.now(timezone.utc))
            )

        if ended_reason:
            query = query.filter(UserSession.ended_reason == ended_reason)

        if start_date:
            query = query.filter(UserSession.login_at >= start_date)

        if end_date:
            query = query.filter(UserSession.login_at <= end_date)

        return query

    def search(
        self,
        limit: int = 50,
        offset: int = 0,
        **filters
    ):
        query = self._apply_filters(
            self.db.query(UserSession),
            **filters
        )

        return (
            query
            .order_by(UserSession.login_at.desc(), UserSession.id.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

    def count(
        self,
        **filters
    ) -> int:
        query = self._apply_filters(
            self.db.query(func.count(UserSession.id)),
            **filters
        )

        return query.scalar() or 0

    def expire_stale(
        self,
        now: Optional[datetime] = None
    ) -> int:
        """
        Backfills ended_at/end_reason on sessions whose token expiry has
        passed but which were never explicitly closed (the user simply
        walked away instead of logging out).

        Purely cosmetic for reporting — is_active() and the active_only
        filter already treat these as dead. Without it, "sessions that
        ended by logout vs. by timeout" would be unanswerable, since the
        timed-out ones would have a NULL reason forever.
        """
        now = now or datetime.now(timezone.utc)

        updated = (
            self.db.query(UserSession)
            .filter(
                UserSession.ended_at.is_(None),
                UserSession.expires_at <= now
            )
            .update(
                {
                    "ended_at": UserSession.expires_at,
                    "ended_reason": SessionEndReason.EXPIRED
                },
                synchronize_session=False
            )
        )

        self.db.commit()

        return updated

    def delete_older_than(
        self,
        cutoff: datetime
    ) -> int:
        """
        Retention purge for closed sessions. Deliberately refuses to
        remove a session that is still live, however old the cutoff.
        """
        deleted = (
            self.db.query(UserSession)
            .filter(
                UserSession.login_at < cutoff,
                or_(
                    UserSession.ended_at.isnot(None),
                    UserSession.expires_at <= datetime.now(timezone.utc)
                )
            )
            .delete(synchronize_session=False)
        )

        self.db.commit()

        return deleted
