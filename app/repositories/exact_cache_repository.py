from datetime import datetime, timezone

from app.models.exact_cache import ExactCache


class ExactCacheRepository:
    def __init__(self, db):
        self.db = db

    def get_by_key(self, cache_key: str):
        return (
            self.db.query(ExactCache)
            .filter(ExactCache.cache_key == cache_key)
            .first()
        )

    def upsert(self, cache_key: str, customer_id: int, response_json: str, expires_at: datetime):
        existing = self.get_by_key(cache_key)

        if existing:
            existing.response = response_json
            existing.expires_at = expires_at
            existing.customer_id = customer_id
        else:
            self.db.add(
                ExactCache(
                    cache_key=cache_key,
                    customer_id=customer_id,
                    response=response_json,
                    expires_at=expires_at
                )
            )

        self.db.commit()

    def delete_expired(self, row: ExactCache):
        """Called when get_by_key() finds a row whose TTL has already passed."""
        self.db.delete(row)
        self.db.commit()

    def delete_for_customer(self, customer_id: int):
        """Wipes the exact-match cache for one tenant only."""
        self.db.query(ExactCache).filter(ExactCache.customer_id == customer_id).delete()
        self.db.commit()

    def delete_all(self):
        """Wipes the exact-match cache for every tenant. Use sparingly —
        prefer delete_for_customer() so clearing one customer's cache
        doesn't cold-cache every other customer too."""
        self.db.query(ExactCache).delete()
        self.db.commit()
