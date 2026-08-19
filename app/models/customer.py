from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Boolean
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey

from sqlalchemy.orm import relationship

from datetime import datetime

from app.database.database import Base


class Customer(Base):
    """
    A tenant in the multi-tenant SaaS model. One Super Admin can create many
    Customers; each Customer owns its own Users, Documents, Conversations,
    Chunks, Embeddings and Semantic Cache entries — all scoped by
    customer_id so tenants can never see each other's data.

    Note: login credentials (email/hashed_password) live only on the
    `users` table, not here, so there is a single source of truth for
    authentication. The `email` field on Customer is the company/contact
    email captured on the onboarding form, not a login.
    """

    __tablename__ = "customers"

    id = Column(
        Integer,
        primary_key=True,
        index=True
    )

    company_name = Column(
        String,
        nullable=False
    )

    address = Column(
        String,
        nullable=True
    )

    mobile = Column(
        String,
        nullable=True
    )

    email = Column(
        String,
        unique=True,
        nullable=False
    )

    # The Super Admin (or other user) who created this customer record.
    created_by = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=True
    )

    is_active = Column(
        Boolean,
        default=True,
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

    # Users belonging to this tenant. Uses foreign_keys because User also
    # has a `created_by`-style link back to the creating admin in some
    # designs — kept explicit here for clarity even though User only has
    # one FK to Customer today.
    users = relationship(
        "User",
        back_populates="customer",
        foreign_keys="User.customer_id"
    )

    documents = relationship(
        "Document",
        back_populates="customer"
    )

    conversations = relationship(
        "Conversation",
        back_populates="customer"
    )
