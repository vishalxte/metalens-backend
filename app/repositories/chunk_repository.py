from sqlalchemy.orm import Session

from app.models.document_chunk import (
    DocumentChunk
)


class ChunkRepository:

    def __init__(
        self,
        db: Session
    ):
        self.db = db

    def create(
        self,
        chunk
    ):

        self.db.add(chunk)
        self.db.commit()
        self.db.refresh(chunk)

        return chunk

    def create_many(
        self,
        chunks: list
    ):
        """
        Inserts all chunks for a document in a single commit instead of
        one commit per chunk. Each object in `chunks` gets its
        auto-generated `id` populated after commit, same as create().
        """
        self.db.add_all(chunks)
        self.db.commit()

        for chunk in chunks:
            self.db.refresh(chunk)

        return chunks

    def delete_by_document(
        self,
        document_id: int
    ):
        """
        Used to clean up any chunks that were already inserted for a
        document whose indexing failed partway through (e.g. the
        embedding batch call errored out) — so a failed upload never
        leaves orphaned chunks with no embeddings behind.
        """
        self.db.query(DocumentChunk).filter(
            DocumentChunk.document_id == document_id
        ).delete()
        self.db.commit()