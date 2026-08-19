from datetime import datetime, timezone

from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey

from app.database.database import Base


class ExactCache(Base):

    __tablename__ = "exact_cache"

    id = Column(
        Integer,
        primary_key=True
    )

    # The full "chat:exact_cache:{customer_id}:{sha256}" string CacheService
    # already builds — unique so one lookup is a direct index hit, same
    cache_key = Column(
        String,
        unique=True,
        nullable=False,
        index=True
    )

    # Denormalized alongside cache_key so clear_for_customer() can delete
    # by a simple indexed equality filter instead of a LIKE/prefix scan
    # as a real indexed column instead of a key-pattern scan).
    customer_id = Column(
        Integer,
        ForeignKey("customers.id"),
        nullable=False,
        index=True
    )

    # JSON-serialized response payload (answer, sources, token_usage,
    response = Column(
        Text,
        nullable=False
    )

    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

    expires_at = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True
    )
