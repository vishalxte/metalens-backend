from app.models.document_chunk import DocumentChunk
from app.models.embedding import Embedding
from app.ai.chunking_service import ChunkingService
from app.services.embedding_service import EmbeddingService
from app.core.logging import logger
import time


class IndexingService:

    def __init__(self, chunk_repo, embedding_repo):
        self.chunk_repo = chunk_repo
        self.embedding_repo = embedding_repo
        self.embedding_service = EmbeddingService()

    def index(self, document):

        if not document.extracted_text:
            logger.warning(
                f"No text to index for document: {document.filename}"
            )
            return

        start_time = time.time()

        chunk_texts = ChunkingService.split_text(document.extracted_text)

        logger.info(
            f"Started indexing '{document.filename}' ({len(chunk_texts)} chunks)"
        )

        try:
            # 1. Insert all chunk rows first (one commit), so we have
            # real chunk ids to attach embeddings to.
            chunk_rows = [
                DocumentChunk(
                    document_id=document.id,
                    customer_id=document.customer_id,
                    chunk_index=idx,
                    chunk_text=chunk_text
                )
                for idx, chunk_text in enumerate(chunk_texts)
            ]

            created_chunks = self.chunk_repo.create_many(chunk_rows)

            # 2. Embed every chunk in a handful of batched OpenAI calls
            # instead of one call per chunk — far fewer requests, far
            # less exposure to a rate limit or transient error killing
            # the run partway through a 100+ chunk document.
            vectors = self.embedding_service.generate_embeddings_batch(
                chunk_texts
            )

            # 3. Insert all embedding rows in one commit.
            embedding_rows = [
                Embedding(
                    chunk_id=chunk.id,
                    customer_id=document.customer_id,
                    embedding=vector
                )
                for chunk, vector in zip(created_chunks, vectors)
            ]

            self.embedding_repo.create_many(embedding_rows)

        except Exception:
            # If anything failed after the chunks were already inserted
            # (e.g. the embeddings API call errored out), don't leave
            # orphaned chunks with no embeddings sitting behind a
            # document that still looks "COMPLETED" — clean them up so
            # the failure is all-or-nothing, and let the caller decide
            # how to report it (document_service marks the document
            # FAILED).
            logger.error(
                f"Indexing failed partway through for '{document.filename}' — "
                f"rolling back its partially-created chunks"
            )
            self.chunk_repo.delete_by_document(document.id)
            raise

        elapsed = time.time() - start_time

        logger.info(
            f"Completed indexing '{document.filename}' "
            f"({len(chunk_texts)} chunks) in {elapsed:.2f} seconds"
        )
