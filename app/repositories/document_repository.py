import os

from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.document_chunk import DocumentChunk
from app.models.embedding import Embedding
from app.core.logging import logger


class DocumentRepository:

    def __init__(self, db: Session):
        self.db = db

    def create(self, document: Document):
        self.db.add(document)
        self.db.commit()
        self.db.refresh(document)
        return document

    def get_all_for_customer(self, customer_id: int):
        """
        Replaces the old get_all(). Tenant-scoped — a customer must only
        ever see their own uploaded documents, never another customer's.
        """
        return (
            self.db.query(Document)
            .filter(Document.customer_id == customer_id)
            .order_by(Document.id.desc())
            .all()
        )

    def get_by_id(self, document_id: int):       # ← NEW
        return (
            self.db.query(Document)
            .filter(Document.id == document_id)
            .first()
        )

    def get_by_id_for_customer(self, document_id: int, customer_id: int):
        """
        Same as get_by_id but scoped to the tenant, so one customer can't
        fetch/delete another customer's document just by guessing an id.
        """
        return (
            self.db.query(Document)
            .filter(
                Document.id == document_id,
                Document.customer_id == customer_id
            )
            .first()
        )

    def get_by_filename(self, filename: str, customer_id: int):
        """
        Duplicate-filename checks are per-tenant, not global — Customer A
        and Customer B can both have a file named "readme.md".
        """
        return (
            self.db.query(Document)
            .filter(
                Document.filename == filename,
                Document.customer_id == customer_id
            )
            .first()
        )

    def delete(self, document_id: int):

        document = (
            self.db.query(Document)
            .filter(Document.id == document_id)
            .first()
        )

        if not document:
            return None

        # Delete embeddings for all chunks
        chunks = (
            self.db.query(DocumentChunk)
            .filter(
                DocumentChunk.document_id == document_id
            )
            .all()
        )

        for chunk in chunks:
            (
                self.db.query(Embedding)
                .filter(
                    Embedding.chunk_id == chunk.id
                )
                .delete()
            )

        # Delete chunks
        (
            self.db.query(DocumentChunk)
            .filter(
                DocumentChunk.document_id == document_id
            )
            .delete()
        )

        # Delete physical file from disk
        if os.path.exists(document.file_path):
            os.remove(document.file_path)
            logger.debug(f"File operation: removed physical file {document.file_path}")

        # Delete document record
        self.db.delete(document)
        self.db.commit()

        return document.filename