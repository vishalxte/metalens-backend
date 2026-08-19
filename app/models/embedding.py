from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import ForeignKey

from sqlalchemy.orm import relationship

from pgvector.sqlalchemy import Vector

from app.database.database import Base


class Embedding(Base):

    __tablename__ = "embeddings"

    id = Column(
        Integer,
        primary_key=True
    )

    chunk_id = Column(
        Integer,
        ForeignKey("document_chunks.id", ondelete="CASCADE")
    )

    # Denormalized customer_id so pgvector similarity search can filter
    # `WHERE customer_id = :cid` directly, with no JOIN through
    # document_chunks -> documents -> customers just to enforce tenant
    # isolation. This is the single most important column for RAG search
    # security in this design.
    customer_id = Column(
        Integer,
        ForeignKey("customers.id"),
        nullable=False
    )

    embedding = Column(
        Vector(1536)
    )

    chunk = relationship(
        "DocumentChunk",
        back_populates="embeddings"
    )
