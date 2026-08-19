"""Frontend Activity Log — kept separate from audit_logs."""
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, BigInteger, String, DateTime, ForeignKey, Index

from app.database.database import Base
from app.core.encryption import EncryptedText


class ActivityAction:
    PAGE_VIEW = "PAGE_VIEW"
    NAVIGATION = "NAVIGATION"
    CLICK = "CLICK"
    QUESTION_SUBMITTED = "QUESTION_SUBMITTED"
    ALL = (PAGE_VIEW, NAVIGATION, CLICK, QUESTION_SUBMITTED)


class ActivityTargetType:
    DOCUMENT = "DOCUMENT"
    USER = "USER"
    CUSTOMER = "CUSTOMER"
    CONVERSATION = "CONVERSATION"
    ALL = (DOCUMENT, USER, CUSTOMER, CONVERSATION)


class ActivityPage:
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

    id = Column(BigInteger, primary_key=True)
    event_time = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    user_email = Column(String(320), nullable=True)
    session_id = Column(String(64), nullable=True)
    action = Column(String(32), nullable=False)
    page = Column(String(64), nullable=True)
    from_page = Column(String(64), nullable=True)
    feature = Column(String(64), nullable=True)
    target_type = Column(String(32), nullable=True)
    target_id = Column(String(128), nullable=True)
    question_text = Column(EncryptedText, nullable=True)

    __table_args__ = (
        Index("ix_activity_logs_event_time", event_time.desc()),
        Index("ix_activity_logs_user_id_event_time", user_id, event_time.desc()),
        Index("ix_activity_logs_session_id_event_time", session_id, event_time),
        Index("ix_activity_logs_action_event_time", action, event_time.desc()),
        Index("ix_activity_logs_target", target_type, target_id),
    )

    def __repr__(self) -> str:
        return f"<ActivityLog {self.action} page={self.page} user_id={self.user_id}>"
