"""
Centralized Audit Service — the ONLY thing in the codebase that writes
audit_logs rows.

Three hard guarantees, unchanged by the schema redesign:

  1. AUDIT NEVER BREAKS BUSINESS. Every public method swallows every
     exception and reports it to the application logger instead. A
     failing audit write can never propagate, can never turn a 200 into
     a 500, and can never roll back a business transaction.

  2. AUDIT WRITES ARE TRANSACTIONALLY INDEPENDENT. Each write opens its
     own short-lived SessionLocal() rather than reusing the request's
     `db`, so a failed audit flush cannot poison the caller's Session,
     and a business rollback cannot erase the record of the attempt that
     caused it. Same pattern as CacheService.

  3. THE CALLER NEVER SUPPLIES IDENTITY. user / session / IP are read
     from contextvars (app/core/request_context.py), never from
     arguments. Callers describe only WHAT happened.

Layering rule:
    Business Service  ->  AuditService  ->  AuditRepository
    (API routes and business repositories never write audit rows.)

Sensitive-data rule: `details` is sanitized on the way in — passwords,
hashes, tokens, question text, answers, chunk text and document contents
are dropped before the row is written. See _sanitize_details().
"""
import json
from datetime import datetime, timezone
from typing import Optional

from app.core.config import settings
from app.core.logging import logger
from app.core.request_context import get_audit_context
from app.database.session import SessionLocal
from app.models.audit_log import (
    AuditLog,
    AuditCategory,
    AuditAction,
    AuditStatus,
    TargetType
)
from app.repositories.audit_repository import AuditRepository


# Any details key containing one of these substrings is redacted rather
# than stored. Substring matching (not exact) so variants like
# "hashed_password", "access_token" or "api_key_value" are all caught.
_SENSITIVE_KEY_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "credential",
    "hashed",
    "question",
    "prompt",
    "answer",
    "content",
    "chunk_text",
    "extracted_text",
    "context",
    "smtp"
)

_REDACTED = "[REDACTED]"

# Exact key names that are known-safe DERIVED metrics and bypass the
# substring rule above.
#
# Without this, "question_length" and "question_sha256" would both be
# redacted by the "question" fragment — but those are precisely the two
# values that let the AI trail be useful WITHOUT storing the question
# (the question itself now lives in activity_logs.question_text).
#
# Exact-match only, never substring, so nothing else slips through.
_SAFE_KEY_ALLOWLIST = frozenset({
    "question_length",
    "question_sha256",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "smtp_host"
})

# Column widths from app/models/audit_log.py. Truncating here rather
# than letting Postgres raise keeps guarantee #1 intact for oversized
# input.
_MAX_LEN = {
    "user_email": 320,
    "session_id": 64,
    "ip_address": 64,
    "category": 32,
    "action": 64,
    "target_type": 32,
    "target_id": 128,
    "status": 16
}


def _truncate(value: Optional[str], field: str) -> Optional[str]:
    if value is None:
        return None

    text = str(value)
    limit = _MAX_LEN.get(field)

    return text[:limit] if limit else text


def _sanitize_details(details: Optional[dict]) -> Optional[dict]:
    """
    Recursively strips anything confidential out of the JSONB payload.

    Two independent protections:
      - key-based redaction against _SENSITIVE_KEY_FRAGMENTS, so a
        caller cannot accidentally persist a password, a token or a raw
        question by naming a field carelessly;
      - a JSON-serializability check, so a stray ORM object or datetime
        cannot make the INSERT fail.
    """
    if not details:
        return None

    def clean(value, key_hint: str = ""):
        lowered = key_hint.lower()

        # Allowlist is checked BEFORE the fragment blocklist.
        if lowered not in _SAFE_KEY_ALLOWLIST and any(
            fragment in lowered for fragment in _SENSITIVE_KEY_FRAGMENTS
        ):
            return _REDACTED

        if isinstance(value, dict):
            return {k: clean(v, k) for k, v in value.items()}

        if isinstance(value, (list, tuple)):
            return [clean(v, key_hint) for v in value]

        if isinstance(value, (str, int, float, bool)) or value is None:
            return value

        if isinstance(value, datetime):
            return value.isoformat()

        return str(value)

    try:
        cleaned = {k: clean(v, k) for k, v in details.items()}
        json.dumps(cleaned)  # prove it survives the JSONB encode
        return cleaned
    except (TypeError, ValueError):
        logger.warning(
            "Audit details payload was not JSON-serializable — storing a "
            "string fallback instead of dropping the audit record",
            extra={"event": "audit_details_unserializable"}
        )
        return {"unserializable_details": str(details)[:2000]}


class AuditService:
    """
    Stateless by design and instantiated once at module import (see
    `audit_service` at the bottom), mirroring how cache_service and
    session_service are already wired. No DI container is introduced.
    """

    # ─────────────────────────────────────────────────────────────
    # Core write path
    # ─────────────────────────────────────────────────────────────
    def log(
        self,
        category: str,
        action: str,
        status: str = AuditStatus.SUCCESS,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        details: Optional[dict] = None,
        # Escape hatches for the handful of events where the actor is
        # known but is NOT the authenticated context user — most notably
        # a failed login, where authentication never happened so the
        # context is empty but we do know which email was attempted.
        user_id_override: Optional[int] = None,
        user_email_override: Optional[str] = None,
        # When False, identity comes ONLY from the overrides above and
        # the context actor is ignored entirely. Needed for
        # AUTHENTICATION events, where the subject of the event is not
        # necessarily the context user: a failed login submitted from a
        # browser that already holds a valid token for someone else
        # would otherwise record the attempted email next to the token
        # holder's user_id — an actively misleading row.
        use_context_actor: bool = True
    ) -> None:
        """
        Writes one audit row. Returns None always — callers must not be
        able to branch on whether auditing succeeded, because that would
        reintroduce the coupling this design exists to prevent.
        """
        try:
            if not settings.AUDIT_ENABLED:
                return

            context = get_audit_context()

            if use_context_actor:
                actor_user_id = (
                    user_id_override
                    if user_id_override is not None
                    else context["user_id"]
                )
                actor_email = user_email_override or context["user_email"]
            else:
                actor_user_id = user_id_override
                actor_email = user_email_override

            entry = AuditLog(
                event_time=datetime.now(timezone.utc),
                user_id=actor_user_id,
                user_email=_truncate(actor_email, "user_email"),
                # Session and IP always come from context — they
                # describe the connection, not the actor, and are valid
                # regardless of who the event is about.
                session_id=_truncate(context["session_id"], "session_id"),
                ip_address=_truncate(context["ip_address"], "ip_address"),
                category=_truncate(category, "category"),
                action=_truncate(action, "action"),
                target_type=_truncate(target_type, "target_type"),
                target_id=_truncate(target_id, "target_id"),
                status=_truncate(status, "status"),
                details=_sanitize_details(details)
            )

            self._persist(entry)

        except Exception:
            # Guarantee #1. Nothing escapes this method — ever.
            self._report_failure(category, action)

    def _persist(self, entry: AuditLog) -> None:
        """
        Own session, own transaction, always closed. Deliberately does
        NOT accept a caller-supplied Session — see guarantee #2.
        """
        db = SessionLocal()

        try:
            AuditRepository(db).create(entry)

            logger.debug(
                f"Audit written — {entry.category}/{entry.action} "
                f"status={entry.status} target={entry.target_type}:{entry.target_id}",
                extra={
                    "event": "audit_written",
                    "audit_category": entry.category,
                    "audit_action": entry.action,
                    "audit_status": entry.status
                }
            )

        except Exception:
            # Roll back only OUR private session. The caller's business
            # transaction is a different Session object entirely.
            try:
                db.rollback()
            except Exception:
                pass
            raise

        finally:
            db.close()

    def _report_failure(self, category: str, action: str) -> None:
        """
        Audit failures are written into the application log and nowhere
        else — nothing is raised, nothing is returned, nothing reaches
        the client.
        """
        try:
            logger.error(
                f"AUDIT WRITE FAILED for {category}/{action} — the business "
                f"operation was NOT affected and completed normally",
                exc_info=True,
                extra={
                    "event": "audit_write_failed",
                    "audit_category": category,
                    "audit_action": action
                }
            )
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────
    # Category helpers — thin wrappers so call sites read as one short,
    # intention-revealing line and cannot pick a mismatched
    # category/action/target combination.
    # ─────────────────────────────────────────────────────────────
    def log_auth(
        self,
        action: str,
        status: str = AuditStatus.SUCCESS,
        user_email: Optional[str] = None,
        user_id: Optional[int] = None,
        details: Optional[dict] = None
    ) -> None:
        """
        Login / Logout / Failed login / OTP events.

        use_context_actor=False: the subject of an auth event is whoever
        the caller names, never whoever happens to be in context.
        """
        self.log(
            category=AuditCategory.AUTHENTICATION,
            action=action,
            status=status,
            target_type=TargetType.USER if user_id is not None else None,
            target_id=str(user_id) if user_id is not None else None,
            details=details,
            user_id_override=user_id,
            user_email_override=user_email,
            use_context_actor=False
        )

    def log_security(
        self,
        action: str,
        status: str = AuditStatus.FAILURE,
        details: Optional[dict] = None,
        user_email: Optional[str] = None
    ) -> None:
        """
        401 / 403 / invalid token / permission denied.

        These are the events that depend on user_email and ip_address:
        most have no user_id at all, because no user was ever resolved.
        """
        self.log(
            category=AuditCategory.SECURITY,
            action=action,
            status=status,
            details=details,
            user_email_override=user_email
        )

    def log_user_event(
        self,
        action: str,
        target_user_id: Optional[int] = None,
        target_email: Optional[str] = None,
        status: str = AuditStatus.SUCCESS,
        details: Optional[dict] = None
    ) -> None:
        """
        Create / Update / Activate / Deactivate / Delete a user.

        Note the asymmetry, which is the whole point of target_*: the
        ACTOR comes from context (whoever is logged in), the TARGET is
        passed explicitly. "USER_DELETED by user 8, target USER 35."
        """
        payload = dict(details or {})

        if target_email:
            payload["target_email"] = target_email

        self.log(
            category=AuditCategory.USER_MANAGEMENT,
            action=action,
            status=status,
            target_type=TargetType.USER,
            target_id=str(target_user_id) if target_user_id is not None else None,
            details=payload
        )

    def log_customer_event(
        self,
        action: str,
        target_customer_id: Optional[int] = None,
        company_name: Optional[str] = None,
        status: str = AuditStatus.SUCCESS,
        details: Optional[dict] = None
    ) -> None:
        """
        Create / Update / Activate / Deactivate / Delete a customer.
        Its own category so tenant-level administration is filterable
        apart from user administration.
        """
        payload = dict(details or {})

        if company_name:
            payload["company_name"] = company_name

        self.log(
            category=AuditCategory.CUSTOMER_MANAGEMENT,
            action=action,
            status=status,
            target_type=TargetType.CUSTOMER,
            target_id=str(target_customer_id) if target_customer_id is not None else None,
            details=payload
        )

    def log_document_event(
        self,
        action: str,
        document_id: Optional[int] = None,
        filename: Optional[str] = None,
        status: str = AuditStatus.SUCCESS,
        details: Optional[dict] = None
    ) -> None:
        """
        Upload / Delete / Parse. Filename is metadata ABOUT the document,
        not document content, so it is safe to record and is what makes
        the trail readable.
        """
        payload = dict(details or {})

        if filename:
            payload["filename"] = filename

        self.log(
            category=AuditCategory.DOCUMENT_MANAGEMENT,
            action=action,
            status=status,
            target_type=TargetType.DOCUMENT,
            target_id=str(document_id) if document_id is not None else None,
            details=payload
        )

    def log_knowledge_base_event(
        self,
        action: str,
        document_id: Optional[int] = None,
        status: str = AuditStatus.SUCCESS,
        details: Optional[dict] = None
    ) -> None:
        """
        Chunk creation / Embedding creation / indexing failure / cache
        clear. Separate category from DOCUMENT_MANAGEMENT because these
        are index-lifecycle events — the ones you look at when search
        results go wrong.
        """
        self.log(
            category=AuditCategory.KNOWLEDGE_BASE,
            action=action,
            status=status,
            target_type=TargetType.DOCUMENT if document_id is not None else None,
            target_id=str(document_id) if document_id is not None else None,
            details=details
        )

    def log_ai_event(
        self,
        action: str = AuditAction.RESPONSE_GENERATED,
        conversation_id: Optional[int] = None,
        status: str = AuditStatus.SUCCESS,
        details: Optional[dict] = None
    ) -> None:
        """
        AI response generated.

        CRITICAL: telemetry ONLY. The question text belongs in
        activity_logs.question_text; the generated answer and retrieved
        chunks are never stored anywhere. _sanitize_details() redacts
        those keys as a second line of defence, but call sites are
        expected to send counts, model names and timings.
        """
        self.log(
            category=AuditCategory.AI,
            action=action,
            status=status,
            target_type=TargetType.CONVERSATION if conversation_id is not None else None,
            target_id=str(conversation_id) if conversation_id is not None else None,
            details=details
        )

    def log_system_event(
        self,
        action: str,
        category: str = AuditCategory.SYSTEM,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        status: str = AuditStatus.SUCCESS,
        details: Optional[dict] = None
    ) -> None:
        """
        Settings / configuration / session revocation. `category` is a
        parameter so the same helper serves SYSTEM and SECURITY without
        two near-identical methods.
        """
        self.log(
            category=category,
            action=action,
            status=status,
            target_type=target_type,
            target_id=target_id,
            details=details
        )


# Module-level singleton, matching cache_service / session_service.
# Import this, not the class:
#     from app.services.audit_service import audit_service
audit_service = AuditService()
