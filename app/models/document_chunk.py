from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import Text
from sqlalchemy import ForeignKey

from sqlalchemy.orm import relationship

from app.database.database import Base


class DocumentChunk(Base):

    __tablename__ = "document_chunks"

    id = Column(
        Integer,
        primary_key=True
    )

    document_id = Column(
        Integer,
        ForeignKey("documents.id", ondelete="CASCADE")
    )

    # Denormalized from documents.customer_id so similarity search can
    # filter with WHERE customer_id = :cid directly on this table, with no
    # JOIN up to documents/customers needed for tenant isolation.
    customer_id = Column(
        Integer,
        ForeignKey("customers.id"),
        nullable=False
    )

    chunk_index = Column(
        Integer
    )

    chunk_text = Column(
        Text
    )

    document = relationship(
        "Document",
        back_populates="chunks"
    )

    embeddings = relationship(
        "Embedding",
        back_populates="chunk",
        cascade="all, delete-orphan"
    )
