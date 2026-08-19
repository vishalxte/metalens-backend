import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from app.database.session import SessionLocal
from app.repositories.exact_cache_repository import ExactCacheRepository
from app.core.config import settings
from app.core.logging import logger
from app.core.timing import log_duration


CACHE_KEY_PREFIX = "chat:exact_cache:"


class CacheService:
    def __init__(self):
        self.ttl = settings.CACHE_TTL_SECONDS

    def _normalize(self, question: str) -> str:
        text = question.strip().lower()
        # Strip trailing punctuation like ? . ! that don't change meaning
        text = re.sub(r"[?.!]+$", "", text.strip())
        # Collapse all internal whitespace
        text = " ".join(text.split())
        return text

    def _build_key(self, question: str, customer_id: int) -> str:
        """
        customer_id is embedded in the key itself (not just hashed away
        with the question text) — this closes a cross-tenant leak: without
        it, Customer A asking "What is the Leave Policy?" and Customer B
        asking the exact same question would collide on the same cache
        row and B would get A's cached answer, even after the pgvector
        semantic_cache was correctly scoped by customer_id.
        """
        normalized = self._normalize(question)
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return f"{CACHE_KEY_PREFIX}{customer_id}:{digest}"

    def get(self, question: str, customer_id: int):
        key = self._build_key(question, customer_id)

        db = SessionLocal()
        try:
            with log_duration(logger, "exact_cache_get", customer_id=customer_id):
                repo = ExactCacheRepository(db)
                row = repo.get_by_key(key)

                if row is None:
                    logger.debug(f"Exact cache MISS — customer_id={customer_id}, key={key}")
                    return None

                if row.expires_at < datetime.now(timezone.utc):
                    # expire on its own. Clean the stale row up while we're here.
                    logger.debug(f"Exact cache EXPIRED — customer_id={customer_id}, key={key}")
                    repo.delete_expired(row)
                    return None

                logger.info(
                    f"Exact cache HIT — customer_id={customer_id}",
                    extra={"event": "cache_hit", "cache_type": "exact", "customer_id": customer_id}
                )
                return json.loads(row.response)
        finally:
            db.close()

    def set(self, question: str, response: dict, customer_id: int):
        key = self._build_key(question, customer_id)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self.ttl)

        db = SessionLocal()
        try:
            with log_duration(logger, "exact_cache_set", customer_id=customer_id):
                repo = ExactCacheRepository(db)
                repo.upsert(
                    cache_key=key,
                    customer_id=customer_id,
                    response_json=json.dumps(response),
                    expires_at=expires_at
                )
            logger.debug(f"Exact cache WRITE — customer_id={customer_id}, key={key}, ttl={self.ttl}s")
        finally:
            db.close()

    def clear_for_customer(self, customer_id: int):
        """Wipes the exact-match cache for one tenant only."""
        db = SessionLocal()
        try:
            ExactCacheRepository(db).delete_for_customer(customer_id)
            logger.info(f"Exact cache cleared for customer_id={customer_id}")
        finally:
            db.close()

    def clear_all(self):
        """Wipes the exact match cache for every tenant. Use sparingly —
        prefer clear_for_customer() so deleting one customer's document
        doesn't cold-cache every other customer too."""
        db = SessionLocal()
        try:
            ExactCacheRepository(db).delete_all()
            logger.warning("Exact cache cleared for ALL tenants")
        finally:
            db.close()


cache_service = CacheService()
