from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime

from app.database.database import Base
from app.core.encryption import EncryptedText


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, nullable=False)
    file_type = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    extracted_text = Column(EncryptedText, nullable=True)
    status = Column(String, default="PENDING", nullable=False)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    customer = relationship("Customer", back_populates="documents")
    creator = relationship("User", back_populates="documents", foreign_keys=[created_by])
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")
