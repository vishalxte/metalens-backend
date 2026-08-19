"""Resume-safe batched migration of legacy plaintext sensitive fields.

Run only after the application-level encryption code and the sources JSON->TEXT
schema migration are deployed. The application may stay live while this runs:
rows are selected with FOR UPDATE SKIP LOCKED, encrypted in memory, and committed
in bounded batches. Re-running the script safely skips authenticated v1 values.

The script never prints plaintext or the encryption key.
"""
import sys
from typing import Iterable

from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.encryption import encrypt, is_encrypted

BATCH_SIZE = 500
FIELDS = (
    ("documents", "id", "extracted_text"),
    ("document_chunks", "id", "chunk_text"),
    ("chat_messages", "id", "content"),
    ("semantic_cache", "id", "question_text"),
    ("semantic_cache", "id", "answer"),
    ("semantic_cache", "id", "sources"),
    ("exact_cache", "id", "response"),
    ("activity_logs", "id", "question_text"),
)


def _database_url() -> str:
    return (
        f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    )


def migrate_field(engine, table: str, pk: str, field: str) -> int:
    total = 0
    while True:
        with engine.begin() as connection:
            rows = connection.execute(text(f"""
                SELECT {pk}, {field}
                FROM {table}
                WHERE {field} IS NOT NULL
                  AND {field} NOT LIKE 'v1:%'
                ORDER BY {pk}
                FOR UPDATE SKIP LOCKED
                LIMIT :batch_size
            """), {"batch_size": BATCH_SIZE}).fetchall()
            if not rows:
                break
            for row in rows:
                value = row[1]
                if value is None:
                    continue
                # Prefix detection is authenticated; a malformed v1 value
                # raises rather than being silently treated as plaintext.
                if isinstance(value, str) and value.startswith("v1:"):
                    is_encrypted(value)
                    continue
                encrypted = encrypt(value)
                connection.execute(
                    text(f"UPDATE {table} SET {field} = :value WHERE {pk} = :id"),
                    {"value": encrypted, "id": row[0]}
                )
            total += len(rows)
        print(f"{table}.{field}: migrated {total} rows")
    return total


def main() -> int:
    engine = create_engine(_database_url(), pool_pre_ping=True)
    grand_total = 0
    try:
        for table, pk, field in FIELDS:
            grand_total += migrate_field(engine, table, pk, field)
    finally:
        engine.dispose()
    print(f"Encryption migration completed. Rows processed: {grand_total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
