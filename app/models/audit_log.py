"""
Audit Log — the security, administrative and business-action record.

Answers: "who performed an important action, on which target, and when,
and did it succeed?"

Twelve columns. Nine were removed from the previous design (role,
customer_id, request_id, user_agent, http_method, http_path,
http_status, duration_ms, created_at) and `timestamp` collapsed into
`event_time`; `resource`/`resource_id` became `target_type`/`target_id`.

WHY user_email AND ip_address SURVIVED THE CUT
──────────────────────────────────────────────
Eight event types have NO user_id, because no user was ever resolved:

    LOGIN_FAILED (unknown email)   OTP_SEND_FAILED   OTP_FAILED
    AUTHENTICATION_FAILURE         INACTIVE_ACCOUNT_ACCESS
    INVALID_TOKEN                  JWT_FAILURE       PERMISSION_DENIED

Without `user_email` a failed login cannot name the account that was
targeted; without `ip_address` a JWT failure cannot say where it came
from. Those rows would read `user_id = NULL, action = LOGIN_FAILED` and
nothing else — brute-force and credential-stuffing would be invisible.
These two columns are what makes the SECURITY category function at all
(OWASP ASVS V7 requires source identification on security events).

`user_email` is also deliberately denormalized: it survives the user row
being deleted, which is exactly when an audit record matters most.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    DateTime,
    ForeignKey,
    Index
)
from sqlalchemy.dialects.postgresql import JSONB

from app.database.database import Base


class AuditCategory:
    AUTHENTICATION = "AUTHENTICATION"
    USER_MANAGEMENT = "USER_MANAGEMENT"
    CUSTOMER_MANAGEMENT = "CUSTOMER_MANAGEMENT"
    DOCUMENT_MANAGEMENT = "DOCUMENT_MANAGEMENT"
    KNOWLEDGE_BASE = "KNOWLEDGE_BASE"
    AI = "AI"
    SECURITY = "SECURITY"
    SYSTEM = "SYSTEM"

    ALL = (
        AUTHENTICATION,
        USER_MANAGEMENT,
        CUSTOMER_MANAGEMENT,
        DOCUMENT_MANAGEMENT,
        KNOWLEDGE_BASE,
        AI,
        SECURITY,
        SYSTEM
    )


class AuditStatus:
    """
    Two values only.

    There used to be a third, DENIED. Migration d9e0f1a2b3c4
    ("redesign audit_logs") removed it and rewrote the existing rows
    with `UPDATE audit_logs SET status = 'FAILURE' WHERE status =
    'DENIED'` — a denied action did not succeed, so it is a FAILURE.

    Five call sites in app/api/dependencies/auth.py were missed by that
    change and kept referencing AuditStatus.DENIED. Because those sites
    are exactly the 403 gates (get_super_admin, get_customer_user,
    get_company_admin, the deactivated-account check and the
    revoked-session check), every permission denial raised
    `AttributeError: type object 'AuditStatus' has no attribute
    'DENIED'` while building the audit record and the client got a 500
    instead of a 403. They now use FAILURE, which is also what the read
    API in app/api/v1/audit.py validates ?status= against via ALL.
    """
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"

    ALL = (SUCCESS, FAILURE)


class TargetType:
    """
    What `target_id` points at. Required for anything that acts ON
    something — without it, "USER_DELETED by user 8" doesn't say who was
    deleted.
    """
    USER = "USER"
    CUSTOMER = "CUSTOMER"
    DOCUMENT = "DOCUMENT"
    CONVERSATION = "CONVERSATION"
    SESSION = "SESSION"

    ALL = (USER, CUSTOMER, DOCUMENT, CONVERSATION, SESSION)


class AuditAction:

    # --- AUTHENTICATION ---
    LOGIN = "LOGIN"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    # Two-factor step. Separate from LOGIN because passing the password
    # is not the same event as completing authentication.
    OTP_SENT = "OTP_SENT"
    OTP_FAILED = "OTP_FAILED"
    OTP_SEND_FAILED = "OTP_SEND_FAILED"

    # --- USER_MANAGEMENT (target_type = USER) ---
    USER_CREATED = "USER_CREATED"
    USER_UPDATED = "USER_UPDATED"
    USER_ACTIVATED = "USER_ACTIVATED"
    USER_DEACTIVATED = "USER_DEACTIVATED"
    USER_DELETED = "USER_DELETED"

    # --- CUSTOMER_MANAGEMENT (target_type = CUSTOMER) ---
    CUSTOMER_CREATED = "CUSTOMER_CREATED"
    CUSTOMER_UPDATED = "CUSTOMER_UPDATED"
    CUSTOMER_ACTIVATED = "CUSTOMER_ACTIVATED"
    CUSTOMER_DEACTIVATED = "CUSTOMER_DEACTIVATED"
    CUSTOMER_DELETED = "CUSTOMER_DELETED"

    # --- DOCUMENT_MANAGEMENT (target_type = DOCUMENT) ---
    DOCUMENT_UPLOADED = "DOCUMENT_UPLOADED"
    DOCUMENT_DELETED = "DOCUMENT_DELETED"
    DOCUMENT_PARSED = "DOCUMENT_PARSED"

    # --- KNOWLEDGE_BASE (target_type = DOCUMENT) ---
    CHUNKS_CREATED = "CHUNKS_CREATED"
    EMBEDDINGS_CREATED = "EMBEDDINGS_CREATED"
    INDEXING_FAILED = "INDEXING_FAILED"
    CACHE_CLEARED = "CACHE_CLEARED"

    # --- AI ---
    # The question itself lives in activity_logs.question_text; this row
    # carries only non-confidential telemetry.
    RESPONSE_GENERATED = "RESPONSE_GENERATED"

    # --- SECURITY ---
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_TOKEN = "INVALID_TOKEN"
    JWT_FAILURE = "JWT_FAILURE"
    AUTHENTICATION_FAILURE = "AUTHENTICATION_FAILURE"
    INACTIVE_ACCOUNT_ACCESS = "INACTIVE_ACCOUNT_ACCESS"
    SESSION_REVOKED = "SESSION_REVOKED"


class AuditLog(Base):

    __tablename__ = "audit_logs"

    id = Column(
        BigInteger,
        primary_key=True
    )

    # THE single timestamp — server-side, timezone-aware.
    event_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    # WHO acted. NULL for pre-authentication events — see user_email.
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )

    # Denormalized, and the only identity available on the eight
    # pre-auth event types. Also survives user deletion.
    user_email = Column(
        String(320),
        nullable=True
    )

    # JWT `jti`. NULL before authentication completes, which is the
    # honest record: no server-issued session existed yet.
    session_id = Column(
        String(64),
        nullable=True
    )

    # Where the request came from. On JWT_FAILURE / INVALID_TOKEN this
    # is the ONLY identifying information that exists.
    ip_address = Column(
        String(64),
        nullable=True
    )

    category = Column(
        String(32),
        nullable=False
    )

    action = Column(
        String(64),
        nullable=False
    )

    # WHAT was acted upon.
    target_type = Column(
        String(32),
        nullable=True
    )

    # String, not Integer: this holds document filenames and session
    # UUIDs as well as numeric user/customer ids.
    target_id = Column(
        String(128),
        nullable=True
    )

    status = Column(
        String(16),
        nullable=False,
        default=AuditStatus.SUCCESS
    )

    # Small, event-specific extras ONLY — never a substitute for the
    # structured columns above. Sanitized on write by AuditService:
    # passwords, tokens, API keys, question text, answers and document
    # content are redacted before they reach the database.
    details = Column(
        JSONB,
        nullable=True
    )

    __table_args__ = (
        Index("ix_audit_logs_event_time", event_time.desc()),
        Index("ix_audit_logs_user_id_event_time", user_id, event_time.desc()),
        Index("ix_audit_logs_session_id_event_time", session_id, event_time.desc()),
        Index("ix_audit_logs_category_event_time", category, event_time.desc()),
        Index("ix_audit_logs_action_event_time", action, event_time.desc()),
        Index("ix_audit_logs_target", target_type, target_id),
    )

    def __repr__(self) -> str:
        return (
            f"<AuditLog {self.category}/{self.action} [{self.status}] "
            f"target={self.target_type}:{self.target_id}>"
        )
