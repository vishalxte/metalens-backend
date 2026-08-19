
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from app.database.database import Base


class Role:
    """
    Plain string role constants (not a native DB enum, to keep migrations
    simple). SUPER_ADMIN manages Customers; CUSTOMER/ADMIN/USER are all
    scoped to a single customer_id.
    """
    SUPER_ADMIN = "SUPER_ADMIN"
    CUSTOMER = "CUSTOMER"
    ADMIN = "ADMIN"
    USER = "USER"


class User(Base):

    __tablename__ = "users"

    id = Column(Integer, primary_key=True)

    email = Column(String, unique=True, nullable=False)

    full_name = Column(String, nullable=False)

    hashed_password = Column(String, nullable=False)

    is_active = Column(Boolean, default=True)

    # Replaces the old is_super_admin boolean. SUPER_ADMIN, CUSTOMER, ADMIN, USER.
    role = Column(
        String,
        nullable=False,
        default=Role.CUSTOMER,
        server_default=Role.CUSTOMER
    )

    # NULL for the Super Admin (tenant-less). Every other user belongs to
    # exactly one Customer (tenant).
    customer_id = Column(
        Integer,
        ForeignKey("customers.id"),
        nullable=True
    )

    customer = relationship(
        "Customer",
        back_populates="users",
        foreign_keys=[customer_id]
    )

    documents = relationship(
        "Document",
        back_populates="creator"
    )

    conversations = relationship(
        "Conversation",
        back_populates="owner",
        cascade="all, delete-orphan"
    )

    @property
    def is_super_admin(self) -> bool:
        """
        Backward-compatible read-only shim. A few older call sites may
        still check `user.is_super_admin` — prefer `user.role ==
        Role.SUPER_ADMIN` (or the get_super_admin dependency) in new code.
        """
        return self.role == Role.SUPER_ADMIN