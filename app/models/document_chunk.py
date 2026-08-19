from sqlalchemy import Column, Integer, Text, ForeignKey
from sqlalchemy.orm import relationship

from app.database.database import Base
from app.core.encryption import EncryptedText


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"))
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    chunk_index = Column(Integer)
    chunk_text = Column(EncryptedText)

    document = relationship("Document", back_populates="chunks")
    embeddings = relationship("Embedding", back_populates="chunk", cascade="all, delete-orphan")
