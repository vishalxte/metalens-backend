from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey

from sqlalchemy.orm import relationship

from datetime import datetime

from app.database.database import Base


class Document(Base):

    __tablename__ = "documents"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    filename = Column(
        String,
        nullable=False
    )

    file_type = Column(
        String,
        nullable=False
    )

    file_path = Column(
        String,
        nullable=False
    )

    extracted_text = Column(
        Text,
        nullable=True
    )

    status = Column(
        String,
        default="PENDING",
        nullable=False
    )

    # Tenant this document belongs to. NOT NULL — every document must be
    # scoped to exactly one customer so search/RAG never crosses tenants.
    customer_id = Column(
        Integer,
        ForeignKey("customers.id"),
        nullable=False
    )

    # Replaces the old owner_id. Same meaning (which user uploaded this
    # document) — renamed to `created_by` to match the rest of the
    # multi-tenant schema (customers.created_by, etc).
    created_by = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=True
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )

    customer = relationship(
        "Customer",
        back_populates="documents"
    )

    creator = relationship(
        "User",
        back_populates="documents",
        foreign_keys=[created_by]
    )

    chunks = relationship(
        "DocumentChunk",
        back_populates="document",
        cascade="all, delete-orphan"
    )
