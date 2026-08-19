from datetime import datetime, timezone
from sqlalchemy import Column, Integer, Text, DateTime, ForeignKey
from pgvector.sqlalchemy import Vector

from app.database.database import Base
from app.core.encryption import EncryptedText, EncryptedJSON


class SemanticCache(Base):
    __tablename__ = "semantic_cache"

    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    question_text = Column(EncryptedText, nullable=False)
    question_embedding = Column(Vector(1536), nullable=False)
    answer = Column(EncryptedText, nullable=False)
    sources = Column(EncryptedJSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_used_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
