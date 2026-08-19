import os
import time

from app.models.document import Document
from app.services.file_parser_service import FileParserService
from app.services.indexing_service import IndexingService
from app.models.document_chunk import DocumentChunk
from app.services.embedding_service import EMBEDDING_MODEL
from app.repositories.chunk_repository import ChunkRepository
from app.repositories.embedding_repository import EmbeddingRepository
from app.core.logging import logger

# NEW IMPORTS
from app.repositories.semantic_cache_repository import SemanticCacheRepository
from app.services.cache_service import cache_service

# Audit Framework
from app.models.audit_log import AuditAction, AuditStatus
from app.services.audit_service import audit_service


class DocumentService:

    def __init__(self, repository):
        self.repository = repository

    def upload_document(self, filename, file_path, user_id, customer_id):

        upload_start = time.time()
        ext = filename.split(".")[-1].lower()

        logger.info(
            f"Processing file: {filename}",
            extra={
                "event": "document_upload_started",
                "doc_filename": filename,
                "file_type": ext,
                "customer_id": customer_id,
                "user_id": user_id,
                "file_path": file_path
            }
        )

        if ext not in ("md", "json", "pdf"):
            audit_service.log_document_event(
                action=AuditAction.DOCUMENT_UPLOADED,
                filename=filename,
                status=AuditStatus.FAILURE,
                details={"reason": "unsupported_file_type", "file_type": ext}
            )
            raise ValueError("Only .md, .json and .pdf files are allowed")

        document = Document(
            filename=filename,
            file_type=ext,
            file_path=file_path,
            created_by=user_id,
            customer_id=customer_id,
            status="PROCESSING"
        )

        # Step 1: Parse file based on type. Status is deliberately left as
        # "PROCESSING" here — it should only become "COMPLETED" once
        # indexing (Step 3) has actually finished successfully, otherwise
        # a document that parsed fine but failed embedding partway
        # through would still show as complete with missing/partial
        # search results.
        try:
            file_size = os.path.getsize(file_path) if os.path.exists(file_path) else None
            logger.debug(f"File operation: saved to disk — {file_path} ({file_size} bytes)")

            if ext == "md":
                extracted_text = FileParserService.parse_md(file_path)
            elif ext == "json":
                extracted_text = FileParserService.parse_json(file_path)
            elif ext == "pdf":
                extracted_text = FileParserService.parse_pdf(file_path)

            document.extracted_text = extracted_text
            logger.info(
                f"{ext.upper()} file parsed successfully: {filename}",
                extra={"event": "document_parsed", "doc_filename": filename, "extracted_chars": len(extracted_text)}
            )

        except Exception as e:
            document.status = "FAILED"
            logger.error(f"Failed to parse file: {filename} — {str(e)}", exc_info=True)
            self.repository.create(document)
            # error_type only, not the message — a parser exception can
            # echo back document content, which must never land in the
            # audit table.
            audit_service.log_document_event(
                action=AuditAction.DOCUMENT_PARSED,
                filename=filename,
                status=AuditStatus.FAILURE,
                details={"file_type": ext, "error_type": type(e).__name__}
            )
            raise e

        # Step 2: Save document record
        saved_document = self.repository.create(document)
        logger.debug(f"Document row saved — id={saved_document.id}, filename={filename}")

        # Character count only — never the extracted text itself.
        audit_service.log_document_event(
            action=AuditAction.DOCUMENT_PARSED,
            document_id=saved_document.id,
            filename=filename,
            details={
                "file_type": ext,
                "extracted_chars": len(extracted_text),
                "file_size_bytes": file_size
            }
        )

        # Step 3: Index chunks and embeddings
        index_start = time.time()
        try:
            db = self.repository.db
            chunk_repo = ChunkRepository(db)
            embedding_repo = EmbeddingRepository(db)
            indexing_service = IndexingService(chunk_repo, embedding_repo)
            indexing_service.index(saved_document)

            saved_document.status = "COMPLETED"
            db.commit()

            index_elapsed_ms = round((time.time() - index_start) * 1000, 2)
            logger.info(
                f"Indexed successfully: {filename} in {index_elapsed_ms}ms",
                extra={"event": "document_indexed", "doc_filename": filename, "duration_ms": index_elapsed_ms}
            )

            # KNOWLEDGE_BASE events: chunk creation and embedding
            # creation. Counts are read back from the repositories rather
            # than from IndexingService, so no existing method signature
            # or return value had to change to obtain them.
            chunk_count = chunk_repo.db.query(
                DocumentChunk
            ).filter(
                DocumentChunk.document_id == saved_document.id
            ).count()

            audit_service.log_knowledge_base_event(
                action=AuditAction.CHUNKS_CREATED,
                document_id=saved_document.id,
                details={"filename": filename, "chunk_count": chunk_count}
            )

            audit_service.log_knowledge_base_event(
                action=AuditAction.EMBEDDINGS_CREATED,
                document_id=saved_document.id,
                details={
                    "filename": filename,
                    "embedding_count": chunk_count,
                    "embedding_model": EMBEDDING_MODEL
                }
            )

        except Exception as e:
            saved_document.status = "FAILED"
            self.repository.db.commit()
            logger.error(f"Indexing failed: {filename} — {str(e)}", exc_info=True)
            audit_service.log_knowledge_base_event(
                action=AuditAction.INDEXING_FAILED,
                document_id=saved_document.id,
                status=AuditStatus.FAILURE,
                details={"filename": filename, "error_type": type(e).__name__}
            )
            raise e

        # Step 4: Invalidate this tenant's caches. Without this, a question
        # asked (and cached, right or wrong) BEFORE this document existed
        # keeps being served straight from exact_cache/semantic_cache for up
        # to CACHE_TTL_SECONDS — retrieval and the LLM never get a chance to
        # look at the newly indexed content at all.
        semantic_repo = SemanticCacheRepository(self.repository.db)
        semantic_repo.delete_by_customer(customer_id)
        cache_service.clear_for_customer(customer_id)

        logger.info(f"Cleared caches for customer {customer_id} after uploading: {filename}")

        audit_service.log_knowledge_base_event(
            action=AuditAction.CACHE_CLEARED,
            document_id=saved_document.id,
            details={
                "reason": "document_uploaded",
                "filename": filename,
                "cache_tiers": ["exact", "semantic"]
            }
        )

        total_elapsed_ms = round((time.time() - upload_start) * 1000, 2)
        logger.info(
            f"Upload pipeline completed for {filename} in {total_elapsed_ms}ms",
            extra={
                "event": "document_upload_completed",
                "doc_filename": filename,
                "customer_id": customer_id,
                "duration_ms": total_elapsed_ms
            }
        )

        # The headline DOCUMENT_MANAGEMENT event, emitted last so it only
        # records a genuinely complete upload (parsed + indexed + caches
        # invalidated), not a half-finished one.
        audit_service.log_document_event(
            action=AuditAction.DOCUMENT_UPLOADED,
            document_id=saved_document.id,
            filename=filename,
            details={
                "file_type": ext,
                "document_status": saved_document.status,
                "uploaded_by_user_id": user_id,
                "customer_id": customer_id
            }
        )

        # Response shape unchanged — same three keys as before.
        return {
            "id": saved_document.id,
            "filename": saved_document.filename,
            "status": saved_document.status
        }

    # NEW METHOD: Tied together to handle full cascade deletion
    def delete_document(self, document_id: int, customer_id: int):

        logger.info(f"Delete requested — document_id={document_id}, customer_id={customer_id}")

        # 1. Fetch document to get the filename — scoped to this tenant,
        # so a customer can't delete (or even probe the existence of)
        # another customer's document by guessing an id.
        document = (
            self.repository.db.query(Document)
            .filter(
                Document.id == document_id,
                Document.customer_id == customer_id
            )
            .first()
        )

        if not document:
            logger.warning(
                f"Delete failed — document_id={document_id} not found for customer_id={customer_id}"
            )
            audit_service.log_document_event(
                action=AuditAction.DOCUMENT_DELETED,
                document_id=document_id,
                status=AuditStatus.FAILURE,
                details={"reason": "not_found_for_customer", "customer_id": customer_id}
            )
            return False

        filename = document.filename

        # 2. Delete the document (Cascade handles chunks & embeddings)
        self.repository.db.delete(document)
        self.repository.db.commit()
        logger.debug(f"File operation: document row + cascaded chunks/embeddings deleted — {filename}")

        # 3. Clean up the Semantic Cache (this tenant only)
        semantic_repo = SemanticCacheRepository(self.repository.db)
        semantic_repo.delete_by_filename(filename, customer_id)

        # 4. Clean up the Exact Cache (Postgres exact_cache table) — this
        # tenant only, so deleting one customer's document doesn't
        # cold-cache every other customer's entries too.
        cache_service.clear_for_customer(customer_id)

        logger.info(
            f"Successfully deleted document and cleared related caches: {filename}",
            extra={"event": "document_deleted", "doc_filename": filename, "customer_id": customer_id}
        )

        audit_service.log_document_event(
            action=AuditAction.DOCUMENT_DELETED,
            document_id=document_id,
            filename=filename,
            details={
                "customer_id": customer_id,
                "cascaded_chunks_and_embeddings": True,
                "caches_cleared": ["exact", "semantic"]
            }
        )

        return True
