from sqlalchemy import text
from app.models.embedding import Embedding
from app.core.logging import logger
from app.core.timing import log_duration


class EmbeddingRepository:

    def __init__(self, db):
        self.db = db

    def create(self, embedding):
        self.db.add(embedding)
        self.db.commit()

    def create_many(self, embeddings: list):
        """Inserts all embeddings for a document in a single commit."""
        self.db.add_all(embeddings)
        self.db.commit()

    def count_chunks_for_customer(self, customer_id):
        """
        Total indexed chunk count for one tenant — used to decide whether
        their whole knowledge base is small enough to hand to the LLM in
        full (no retrieval/ranking at all) instead of trying to guess
        which handful of chunks matter for a given question.
        """
        result = self.db.execute(
            text(
                "SELECT COUNT(*) FROM document_chunks WHERE customer_id = :customer_id"
            ),
            {"customer_id": customer_id}
        )
        return result.scalar() or 0

    def get_all_chunks_for_customer(self, customer_id):
        """
        Every chunk for a tenant, in original reading order (by document,
        then position within it) — used instead of similarity_search()
        when the tenant's whole knowledge base is small enough to just
        send in full. Reading order (rather than relevance order) matters
        here since the LLM benefits from seeing a document the way a
        person would read it, not shuffled by an unrelated ranking.
        """
        sql = text(
            """
            SELECT
                dc.id AS chunk_id,
                dc.chunk_text,
                d.filename
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE dc.customer_id = :customer_id
            ORDER BY dc.document_id ASC, dc.chunk_index ASC
            """
        )

        result = self.db.execute(sql, {"customer_id": customer_id})
        rows = result.fetchall()

        return [
            type("Row", (), {
                "chunk_id": row[0],
                "chunk_text": row[1],
                "filename": row[2],
                "distance": None  # not ranked — this is the full document
            })()
            for row in rows
        ]

    def similarity_search(self, query_embedding, customer_id, top_k=20):
        """
        customer_id is required (not optional) — this is the query that
        enforces tenant isolation for RAG retrieval. It filters directly
        on embeddings.customer_id (denormalized column, no JOIN needed for
        the isolation check itself; the JOINs below are only to fetch
        chunk_text/filename for the response).
        """

        vector_str = (
            "[" +
            ",".join(map(str, query_embedding)) +
            "]"
        )

        sql = text(
            """
            SELECT
                dc.id AS chunk_id,
                dc.chunk_text,
                d.filename,
                e.embedding <=> CAST(:query AS vector) AS distance
            FROM embeddings e
            JOIN document_chunks dc ON dc.id = e.chunk_id
            JOIN documents d ON d.id = dc.document_id
            WHERE e.customer_id = :customer_id
            ORDER BY distance ASC
            LIMIT :top_k
            """
        )

        with log_duration(logger, "vector_similarity_search", customer_id=customer_id, top_k=top_k):
            result = self.db.execute(
                sql,
                {
                    "query": vector_str,
                    "customer_id": customer_id,
                    "top_k": top_k
                }
            )

            rows = result.fetchall()

        logger.debug(f"vector_similarity_search: {len(rows)} rows returned for customer_id={customer_id}")

        # Return as list of objects with .chunk_id, .chunk_text, .filename, .distance
        return [
            type("Row", (), {
                "chunk_id": row[0],
                "chunk_text": row[1],
                "filename": row[2],
                "distance": row[3]
            })()
            for row in rows
        ]

    def keyword_search(self, question, customer_id, limit=10):
        """
        Full-text keyword search — a complement to vector similarity, not
        a replacement. Vector search ranks chunks by *overall* semantic
        topic, so a chunk whose dominant subject is something else (e.g.
        a general project-overview table that only has one row about
        "Pre-Bid Query Submission Date") can rank outside the vector
        top-k even when it contains the exact fact the question needs.
        This catches those chunks by matching actual words in the
        question against the chunk text, independent of embedding rank.

        Deliberately OR, not AND: plainto_tsquery() ANDs every word in the
        question together, so a question like "pre-bid meeting detail"
        would require a chunk to contain "pre-bid" AND "meeting" AND
        "detail" all at once — a chunk that only says "Pre-Bid Query
        Submission Date" (no "meeting", no "detail" nearby) would never
        match, even though it's exactly the fact being asked about. We
        build the same tsquery Postgres would, then flip its top-level
        AND (&) into OR (|) so a chunk only needs to share ONE meaningful
        word with the question — a much better fit for a recall pass.
        """

        tsq_row = self.db.execute(
            text("SELECT plainto_tsquery('english', :question)::text"),
            {"question": question}
        ).first()

        raw_tsquery = tsq_row[0] if tsq_row else ""

        if not raw_tsquery:
            # Question had no indexable words at all (e.g. all stopwords)
            return []

        or_tsquery = raw_tsquery.replace(" & ", " | ")

        sql = text(
            """
            SELECT
                dc.id AS chunk_id,
                dc.chunk_text,
                d.filename,
                ts_rank_cd(
                    to_tsvector('english', dc.chunk_text),
                    to_tsquery('english', :tsquery)
                ) AS rank
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE dc.customer_id = :customer_id
              AND to_tsvector('english', dc.chunk_text)
                  @@ to_tsquery('english', :tsquery)
            ORDER BY rank DESC
            LIMIT :limit
            """
        )

        with log_duration(logger, "keyword_search", customer_id=customer_id, limit=limit):
            result = self.db.execute(
                sql,
                {
                    "customer_id": customer_id,
                    "tsquery": or_tsquery,
                    "limit": limit
                }
            )

            rows = result.fetchall()

        logger.debug(f"keyword_search: {len(rows)} rows returned for customer_id={customer_id}")

        return [
            type("Row", (), {
                "chunk_id": row[0],
                "chunk_text": row[1],
                "filename": row[2],
                "distance": None  # not ranked by vector distance
            })()
            for row in rows
        ]
