from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey

from sqlalchemy.orm import relationship

from datetime import datetime

from app.database.database import Base


class Conversation(Base):

    __tablename__ = "conversations"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    # Short auto-generated label shown in the sidebar, e.g. derived from the
    # first question asked in this conversation ("What is the Faer stratagem
    # bank?" -> "What is the Faer stratagem bank"). Kept editable-friendly
    # (plain string) in case you want a rename feature later.
    title = Column(
        String,
        nullable=False,
        default="New Chat"
    )

    owner_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False
    )

    # Tenant this conversation belongs to. Kept alongside owner_id
    # deliberately: owner_id scopes to the specific user, customer_id
    # scopes to the tenant — both are enforced together (see
    # ConversationRepository) for defense-in-depth.
    customer_id = Column(
        Integer,
        ForeignKey("customers.id"),
        nullable=False
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    owner = relationship(
        "User",
        back_populates="conversations"
    )

    customer = relationship(
        "Customer",
        back_populates="conversations"
    )

    messages = relationship(
        "ChatMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at"
    )