from datetime import datetime, timezone

from sqlalchemy import text

from app.models.semantic_cache import SemanticCache


class SemanticCacheRepository:

    def __init__(self, db):
        self.db = db

    def find_similar(self, query_embedding, customer_id, similarity_threshold=0.95):
        """
        customer_id is required. Without it, a semantically-similar cached
        answer from Customer A could be served to Customer B — this is the
        exact cross-tenant leak the PDF design calls out explicitly.
        """

        distance_threshold = 1 - similarity_threshold

        vector_str = (
            "[" +
            ",".join(map(str, query_embedding)) +
            "]"
        )

        sql = text(
            """
            SELECT
                id,
                question_text,
                answer,
                sources,
                question_embedding <=> CAST(:query AS vector) AS distance
            FROM semantic_cache
            WHERE customer_id = :customer_id
              AND question_embedding <=> CAST(:query AS vector) <= :distance_threshold
            ORDER BY distance ASC
            LIMIT 1
            """
        )

        result = self.db.execute(
            sql,
            {
                "query": vector_str,
                "customer_id": customer_id,
                "distance_threshold": distance_threshold
            }
        )

        row = result.fetchone()

        if row is None:
            return None

        return type("SemanticCacheHit", (), {
            "id": row[0],
            "question_text": row[1],
            "answer": row[2],
            "sources": row[3],
            "distance": row[4],
            "similarity": round(1 - row[4], 4)
        })()

    def create(self, question_text, question_embedding, answer, sources, customer_id):

        entry = SemanticCache(
            customer_id=customer_id,
            question_text=question_text,
            question_embedding=question_embedding,
            answer=answer,
            sources=sources
        )

        self.db.add(entry)
        self.db.commit()

        return entry

    def touch_last_used(self, cache_id):

        self.db.execute(
            text(
                "UPDATE semantic_cache SET last_used_at = :now WHERE id = :cache_id"
            ),
            {
                "now": datetime.now(timezone.utc),
                "cache_id": cache_id
            }
        )
        self.db.commit()

    def delete_stale(self, older_than_days: int = 30):
        """Weekly cleanup: remove entries not reused in `older_than_days` days."""

        result = self.db.execute(
            text(
                """
                DELETE FROM semantic_cache
                WHERE last_used_at < NOW() - (:days || ' days')::interval
                """
            ),
            {"days": older_than_days}
        )
        self.db.commit()

        return result.rowcount

    def delete_by_customer(self, customer_id: int):
        """
        Wipes every cached answer for one tenant, regardless of which
        filename it came from. Used when a NEW document is uploaded —
        any previously cached answer (including ones cached as a wrong
        guess or as a "not found" fallback) could now be answerable
        differently once the new document is indexed, so it's safer to
        drop the whole tenant's semantic cache than to leave stale
        answers reachable for up to the cache's normal lifetime.
        """
        self.db.execute(
            text(
                "DELETE FROM semantic_cache WHERE customer_id = :customer_id"
            ),
            {"customer_id": customer_id}
        )
        self.db.commit()

    def delete_by_filename(self, filename: str, customer_id: int):
        """
        Deletes any cached answer (for this tenant only) that used this
        specific filename as a source. Scoped by customer_id too, since
        filenames are no longer globally unique across tenants.
        """
        sql = text(
            """
            DELETE FROM semantic_cache
            WHERE customer_id = :customer_id
              AND EXISTS (
                SELECT 1
                FROM jsonb_array_elements(sources::jsonb) AS source
                WHERE source->>'filename' = :filename
            )
            """
        )
        self.db.execute(sql, {"filename": filename, "customer_id": customer_id})
        self.db.commit()
