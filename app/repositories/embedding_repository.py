import re

from sqlalchemy import text

from app.models.embedding import Embedding
from app.core.encryption import decrypt_stored_value
from app.core.logging import logger
from app.core.timing import log_duration


_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")


class EmbeddingRepository:
    def __init__(self, db):
        self.db = db

    def create(self, embedding):
        self.db.add(embedding)
        self.db.commit()

    def create_many(self, embeddings: list):
        self.db.add_all(embeddings)
        self.db.commit()

    def count_chunks_for_customer(self, customer_id):
        return self.db.execute(text("SELECT COUNT(*) FROM document_chunks WHERE customer_id = :customer_id"),
                               {"customer_id": customer_id}).scalar() or 0

    def get_all_chunks_for_customer(self, customer_id):
        rows = self.db.execute(text("""
            SELECT dc.id, dc.chunk_text, d.filename
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE dc.customer_id = :customer_id
            ORDER BY dc.document_id ASC, dc.chunk_index ASC
        """), {"customer_id": customer_id}).fetchall()
        return [type("Row", (), {
            "chunk_id": row[0], "chunk_text": decrypt_stored_value(row[1]),
            "filename": row[2], "distance": None
        })() for row in rows]

    def similarity_search(self, query_embedding, customer_id, top_k=20):
        vector_str = "[" + ",".join(map(str, query_embedding)) + "]"
        with log_duration(logger, "vector_similarity_search", customer_id=customer_id, top_k=top_k):
            rows = self.db.execute(text("""
                SELECT dc.id, dc.chunk_text, d.filename,
                       e.embedding <=> CAST(:query AS vector) AS distance
                FROM embeddings e
                JOIN document_chunks dc ON dc.id = e.chunk_id
                JOIN documents d ON d.id = dc.document_id
                WHERE e.customer_id = :customer_id
                ORDER BY distance ASC
                LIMIT :top_k
            """), {"query": vector_str, "customer_id": customer_id, "top_k": top_k}).fetchall()
        logger.debug(f"vector_similarity_search: {len(rows)} rows returned for customer_id={customer_id}")
        return [type("Row", (), {
            "chunk_id": row[0], "chunk_text": decrypt_stored_value(row[1]),
            "filename": row[2], "distance": row[3]
        })() for row in rows]

    def keyword_search(self, question, customer_id, limit=10):
        """Keyword recall over decrypted tenant-scoped chunks.

        PostgreSQL full-text operators cannot inspect encrypted TEXT, so this
        intentionally moves only the tenant's ciphertext rows into application
        memory, decrypts them, and applies the same OR-style keyword intent.
        """
        terms = [t.lower() for t in _WORD_RE.findall(question or "") if len(t) >= 2]
        if not terms:
            return []
        rows = self.db.execute(text("""
            SELECT dc.id, dc.chunk_text, d.filename
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE dc.customer_id = :customer_id
        """), {"customer_id": customer_id}).fetchall()
        ranked = []
        for row in rows:
            chunk_text = decrypt_stored_value(row[1]) or ""
            lowered = chunk_text.lower()
            score = sum(lowered.count(term) for term in terms)
            if score:
                ranked.append((score, row[0], chunk_text, row[2]))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [type("Row", (), {
            "chunk_id": row[1], "chunk_text": row[2], "filename": row[3], "distance": None
        })() for row in ranked[:limit]]
