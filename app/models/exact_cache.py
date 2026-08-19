from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey

from app.database.database import Base
from app.core.encryption import EncryptedText


class ExactCache(Base):
    __tablename__ = "exact_cache"

    id = Column(Integer, primary_key=True)
    cache_key = Column(String, unique=True, nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False, index=True)
    response = Column(EncryptedText, nullable=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
