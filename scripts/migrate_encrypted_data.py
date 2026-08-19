"""Resume-safe batched migration of legacy plaintext sensitive fields.

Run after the application-level encryption code and `alembic upgrade head`.
The application may remain live: rows are selected with FOR UPDATE SKIP LOCKED,
encrypted in memory, and committed in bounded batches. Re-running safely skips
already authenticated v1 values. The script never prints plaintext or keys.
"""
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


def _validate_existing_ciphertexts(connection, table: str, field: str) -> None:
    """Fail closed if any existing v1-prefixed value is malformed/tampered."""
    rows = connection.execute(text(
        f"SELECT {field} FROM {table} WHERE {field} LIKE 'v1:%'"
    )).fetchall()
    for row in rows:
        is_encrypted(row[0])


def migrate_field(engine, table: str, pk: str, field: str) -> int:
    total = 0
    with engine.connect() as connection:
        _validate_existing_ciphertexts(connection, table, field)

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
