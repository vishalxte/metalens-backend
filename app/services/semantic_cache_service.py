from app.repositories.semantic_cache_repository import (
    SemanticCacheRepository
)

from app.core.config import settings
from app.core.logging import logger
from app.core.timing import log_duration


class SemanticCacheService:

    def __init__(self, db):

        self.repository = SemanticCacheRepository(db)
        self.similarity_threshold = settings.SEMANTIC_CACHE_SIMILARITY_THRESHOLD

    def get(self, question_embedding, customer_id):
        """
        Look up a semantically similar cached answer, scoped to one
        tenant. Returns a response dict on hit, or None on miss.
        """

        with log_duration(logger, "semantic_cache_lookup", customer_id=customer_id):
            hit = self.repository.find_similar(
                query_embedding=question_embedding,
                customer_id=customer_id,
                similarity_threshold=self.similarity_threshold
            )

        if hit is None:
            logger.debug(f"Semantic cache MISS — customer_id={customer_id}")
            return None

        self.repository.touch_last_used(hit.id)

        logger.info(
            f"Semantic cache HIT — customer_id={customer_id}, similarity={hit.similarity}",
            extra={
                "event": "cache_hit",
                "cache_type": "semantic",
                "customer_id": customer_id,
                "similarity": hit.similarity
            }
        )

        return {
            "answer": hit.answer,
            "token_usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            },
            "sources": hit.sources,
            "cache": {
                "hit": True,
                "type": "semantic",
                "similarity": hit.similarity,
                "matched_question": hit.question_text
            }
        }

    def set(self, question, question_embedding, answer, sources, customer_id):
        """
        Persist a new question/answer pair for future semantic reuse,
        scoped to the tenant that asked it.
        """

        self.repository.create(
            question_text=question,
            question_embedding=question_embedding,
            answer=answer,
            sources=sources,
            customer_id=customer_id
        )
        logger.debug(f"Semantic cache WRITE — customer_id={customer_id}")


semantic_cache_service = None  # instantiated per-request in Step 6, needs a db session
