"""
Frontend Activity Log — what the user did inside the application.

Answers one question: "what did this user do in the UI?"

Kept strictly separate from audit_logs, which answers a different
question ("who performed an important action, on what, and when?").
Activity is high-volume behavioural telemetry supplied by the browser;
audit is a security record produced by the server. Different retention,
different consumers, different trust level.

DESIGN: DELIBERATELY MINIMAL
────────────────────────────
Nine columns, one timestamp, four actions. Everything technical that
used to live here — ip_address, user_agent, request_id, role,
customer_id, metadata_json, duration_ms, and the three separate time
columns — has been removed.

Why one timestamp: the table previously carried `timestamp` (server),
`client_timestamp` (browser) and `created_at` (row insert). Three
near-identical values invited the wrong one being used in a query and
the browser clock being trusted for ordering. `event_time` is the single
server-side authority.

Identity is `user_id` + `session_id` only. Both are set SERVER-SIDE by
ActivityService from the request context — the client cannot claim to be
another user, and `session_id` is the JWT `jti`, so activity joins
directly to user_sessions and audit_logs.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    Text,
    DateTime,
    ForeignKey,
    Index
)

from app.database.database import Base


class ActivityAction:
    """
    The complete set. Nothing else is accepted — ActivityService rejects
    unknown values rather than storing them, so this list stays the
    definition of what the table can contain.
    """

    # The user opened a page/panel.
    PAGE_VIEW = "PAGE_VIEW"

    # The user moved between pages. `from_page` carries the origin.
    NAVIGATION = "NAVIGATION"

    # A meaningful UI action. `feature` carries what was clicked
    # (UPLOAD_DOCUMENT, DELETE_DOCUMENT, EXPORT...). This is NOT raw
    # mouse-click capture — only named, deliberate interactions.
    CLICK = "CLICK"

    # The user asked the assistant something. `question_text` carries
    # the question verbatim; this is the only action that populates it.
    QUESTION_SUBMITTED = "QUESTION_SUBMITTED"

    ALL = (PAGE_VIEW, NAVIGATION, CLICK, QUESTION_SUBMITTED)


class ActivityTargetType:
    """
    What `target_id` points at, for actions performed ON something —
    "deleted WHICH document", "deactivated WHICH user".

    Values deliberately match audit_log.TargetType so the two tables read
    the same way, but the class is defined locally rather than imported:
    activity_logs and audit_logs are separate modules by design, and a
    shared import would couple them for the sake of five strings.
    """
    DOCUMENT = "DOCUMENT"
    USER = "USER"
    CUSTOMER = "CUSTOMER"
    CONVERSATION = "CONVERSATION"

    ALL = (DOCUMENT, USER, CUSTOMER, CONVERSATION)


class ActivityPage:
    """
    Canonical page names. The UI renders Documents/Users/Chat as panels
    inside one route rather than as separate routes, so these are
    logical names, not URL paths — which also means they survive any
    future routing change.
    """
    DASHBOARD = "DASHBOARD"
    CHAT = "CHAT"
    DOCUMENTS = "DOCUMENTS"
    KNOWLEDGE_BASE = "KNOWLEDGE_BASE"
    SETTINGS = "SETTINGS"
    REPORTS = "REPORTS"
    USERS = "USERS"
    CUSTOMERS = "CUSTOMERS"
    LOGIN = "LOGIN"


class ActivityLog(Base):

    __tablename__ = "activity_logs"

    id = Column(
        BigInteger,
        primary_key=True
    )

    # THE single timestamp. Server-side, timezone-aware, authoritative.
    event_time = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    # SET NULL, not CASCADE — deleting a user should not silently erase
    # the record of what they did.
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True
    )

    # Denormalized alongside user_id so a row stays readable after the
    # user is deleted (user_id goes NULL, this does not) and so the log
    # can be scanned without joining `users`. Stamped server-side from
    # the request context — never accepted from the client.
    user_email = Column(
        String(320),
        nullable=True
    )

    # The JWT `jti`. Same value as user_sessions.session_id and
    # audit_logs.session_id, so one login reconstructs across all three.
    session_id = Column(
        String(64),
        nullable=True
    )

    action = Column(
        String(32),
        nullable=False
    )

    page = Column(
        String(64),
        nullable=True
    )

    # NAVIGATION only — where the user came from.
    from_page = Column(
        String(64),
        nullable=True
    )

    # CLICK only — which feature was used.
    feature = Column(
        String(64),
        nullable=True
    )

    # WHAT the action was performed on. `feature` says what the user
    # did ("DELETE_DOCUMENT"); these say which row they did it to.
    #
    # NULL when nothing exists yet to point at — UPLOAD_DOCUMENT and
    # CREATE_USER are recorded at click time, before the document or
    # user has an id. That NULL is meaningful, not missing data.
    target_type = Column(
        String(32),
        nullable=True
    )

    # String, not Integer, matching audit_logs.target_id — so a
    # filename or UUID fits as easily as a numeric id.
    target_id = Column(
        String(128),
        nullable=True
    )

    # QUESTION_SUBMITTED only. Text, not String: questions have no
    # sensible length limit and truncating one would corrupt the record
    # of what was actually asked.
    #
    # NOTE: stored verbatim and NOT sanitized. audit_logs redacts
    # question text by design; this column exists precisely to keep it,
    # which means anything a user pastes into chat is retained here and
    # will appear in database backups.
    question_text = Column(
        Text,
        nullable=True
    )

    __table_args__ = (
        # Every realistic query is "filter by one dimension, newest
        # first". Composite-with-event_time-DESC so the filter and the
        # sort are both served by the index.
        Index("ix_activity_logs_event_time", event_time.desc()),
        Index("ix_activity_logs_user_id_event_time", user_id, event_time.desc()),
        Index("ix_activity_logs_session_id_event_time", session_id, event_time),
        Index("ix_activity_logs_action_event_time", action, event_time.desc()),
        # "everything that happened to document 42" — same shape as the
        # equivalent index on audit_logs.
        Index("ix_activity_logs_target", target_type, target_id),
    )

    def __repr__(self) -> str:
        return (
            f"<ActivityLog {self.action} page={self.page} "
            f"user_id={self.user_id}>"
        )
