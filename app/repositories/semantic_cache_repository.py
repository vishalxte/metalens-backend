import json
from datetime import datetime, timezone

from sqlalchemy import text

from app.models.semantic_cache import SemanticCache
from app.core.encryption import decrypt_stored_value


class SemanticCacheRepository:
    def __init__(self, db):
        self.db = db

    def find_similar(self, query_embedding, customer_id, similarity_threshold=0.95):
        distance_threshold = 1 - similarity_threshold
        vector_str = "[" + ",".join(map(str, query_embedding)) + "]"
        result = self.db.execute(text("""
            SELECT id, question_text, answer, sources,
                   question_embedding <=> CAST(:query AS vector) AS distance
            FROM semantic_cache
            WHERE customer_id = :customer_id
              AND question_embedding <=> CAST(:query AS vector) <= :distance_threshold
            ORDER BY distance ASC
            LIMIT 1
        """), {
            "query": vector_str,
            "customer_id": customer_id,
            "distance_threshold": distance_threshold
        })
        row = result.fetchone()
        if row is None:
            return None
        question_text = decrypt_stored_value(row[1])
        answer = decrypt_stored_value(row[2])
        sources_text = decrypt_stored_value(row[3])
        try:
            sources = json.loads(sources_text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Invalid semantic cache sources JSON") from exc
        return type("SemanticCacheHit", (), {
            "id": row[0], "question_text": question_text, "answer": answer,
            "sources": sources, "distance": row[4], "similarity": round(1 - row[4], 4)
        })()

    def create(self, question_text, question_embedding, answer, sources, customer_id):
        entry = SemanticCache(customer_id=customer_id, question_text=question_text,
                              question_embedding=question_embedding, answer=answer, sources=sources)
        self.db.add(entry)
        self.db.commit()
        return entry

    def touch_last_used(self, cache_id):
        self.db.execute(text("UPDATE semantic_cache SET last_used_at = :now WHERE id = :cache_id"),
                        {"now": datetime.now(timezone.utc), "cache_id": cache_id})
        self.db.commit()

    def delete_stale(self, older_than_days: int = 30):
        result = self.db.execute(text("""
            DELETE FROM semantic_cache
            WHERE last_used_at < NOW() - (:days || ' days')::interval
        """), {"days": older_than_days})
        self.db.commit()
        return result.rowcount

    def delete_by_customer(self, customer_id: int):
        self.db.execute(text("DELETE FROM semantic_cache WHERE customer_id = :customer_id"),
                        {"customer_id": customer_id})
        self.db.commit()

    def delete_by_filename(self, filename: str, customer_id: int):
        """Encrypted sources cannot be searched with PostgreSQL JSON operators; filter after decryption."""
        rows = self.db.execute(text("""
            SELECT id, sources FROM semantic_cache WHERE customer_id = :customer_id
        """), {"customer_id": customer_id}).fetchall()
        ids = []
        for row in rows:
            sources_text = decrypt_stored_value(row[1])
            try:
                sources = json.loads(sources_text)
            except (TypeError, json.JSONDecodeError):
                continue
            if any(isinstance(source, dict) and source.get("filename") == filename for source in sources):
                ids.append(row[0])
        if ids:
            self.db.execute(text("DELETE FROM semantic_cache WHERE customer_id = :customer_id AND id = ANY(:ids)"),
                            {"customer_id": customer_id, "ids": ids})
            self.db.commit()
