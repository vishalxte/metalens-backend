# Application-Level Encryption

The backend encrypts exactly these PostgreSQL fields with AES-256-GCM:

- `documents.extracted_text`
- `document_chunks.chunk_text`
- `chat_messages.content`
- `semantic_cache.question_text`
- `semantic_cache.answer`
- `semantic_cache.sources`
- `exact_cache.response`
- `activity_logs.question_text`

Embeddings, `question_embedding`, relational IDs, customer/user IDs, roles, statuses, timestamps, cache keys, and `audit_logs` remain unchanged.

## Key

Set `APP_ENCRYPTION_KEY` to Base64 for exactly 32 random bytes. Generate one with:

```bash
python -c "import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

The key is validated during Pydantic Settings startup. Missing, invalid Base64, or non-32-byte values fail startup. The key is never stored in PostgreSQL or the Docker image.

For Docker Compose, provide it through `.env.docker` or another runtime secret injection mechanism. Do not commit the real value.

## Ciphertext

Values use:

`v1:<key_version>:<base64_nonce>:<base64_ciphertext_and_tag>`

AES-GCM uses a fresh 96-bit cryptographically secure nonce for every encryption operation. Authentication failures are raised instead of returning potentially corrupted plaintext.

The key lookup is centralized in `app/core/encryption.py`, so future key-version/KMS/Key Vault integration can be added there without changing application call sites.

## Migration

1. Deploy the application code and run `alembic upgrade head`.
2. Run `python -m scripts.migrate_encrypted_data` from the backend environment.
3. The data migration processes 500 rows per transaction and uses `FOR UPDATE SKIP LOCKED`. It can be safely restarted and skips already authenticated `v1` values.
4. Verify the eight scoped columns contain no remaining plaintext.

`semantic_cache.sources` is migrated from JSON to TEXT first. The separate data migration then encrypts its existing JSON text. The ORM converts decrypted JSON text back to the original Python list/dict shape.

## Backup and recovery

Database backups contain ciphertext for the eight protected fields. The encryption key is deliberately absent from PostgreSQL backups and Docker images.

Full disaster recovery therefore requires both:

**database backup + secure encryption-key recovery**

Losing the encryption key makes encrypted historical data unrecoverable.

## RAG compatibility

Chunk embeddings remain plaintext pgvector vectors and remain searchable. Retrieved encrypted chunk text is decrypted only in application memory before it reaches the LLM. PostgreSQL full-text keyword search cannot inspect encrypted chunk text, so the keyword-recall path performs tenant-scoped application-side matching after decryption.

## Logging

Sensitive question/chunk/answer text is not logged. Chat summaries record question length rather than the question itself. SQL echo/debug logging should remain disabled in production.
