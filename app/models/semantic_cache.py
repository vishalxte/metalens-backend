from datetime import datetime, timezone

from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import Text
from sqlalchemy import DateTime
from sqlalchemy import JSON
from sqlalchemy import ForeignKey

from pgvector.sqlalchemy import Vector

from app.database.database import Base


class SemanticCache(Base):

    __tablename__ = "semantic_cache"

    id = Column(
        Integer,
        primary_key=True
    )

    # Without this, a cached answer generated for Customer A's documents
    # could be served back to Customer B for a similar-sounding question —
    # a cross-tenant data leak. Every lookup/write must filter on this.
    customer_id = Column(
        Integer,
        ForeignKey("customers.id"),
        nullable=False
    )

    question_text = Column(
        Text,
        nullable=False
    )

    question_embedding = Column(
        Vector(1536),
        nullable=False
    )

    answer = Column(
        Text,
        nullable=False
    )

    sources = Column(
        JSON,
        nullable=False,
        default=list
    )

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

    last_used_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )