"""
Diagnose (and optionally repair) chunks that have no embedding row.

WHY THIS EXISTS
───────────────
Migration a1b2c3d4e5f6 ("switch embeddings to openai text-embedding-3-small")
runs `TRUNCATE TABLE embeddings` — it had to, because the old 384-dim
all-MiniLM vectors are the wrong shape for the new 1536-dim column and
cannot be converted. Its own docstring says:

    "After this migration runs, all documents must be re-uploaded /
     re-indexed ... existing document rows and chunk text are left
     intact, only the embeddings themselves and the semantic cache are
     cleared."

Any document indexed BEFORE that migration therefore still has all of
its `document_chunks` rows (chunk_text intact) but ZERO `embeddings`
rows. The visible symptom is a hybrid search that silently degrades to
keyword-only:

    vector_chunks:  0      <- embeddings table is empty for this tenant
    keyword_chunks: 20     <- document_chunks is fine

No error is ever raised, because `similarity_search()` legitimately
returns an empty list and `_merge_retrieval_results()` happily proceeds
with only the keyword hits.

WHY RE-UPLOADING IS NOT NECESSARY
─────────────────────────────────
The chunk TEXT survived the truncate. Only the vectors were lost. So the
missing embeddings can be regenerated directly from the existing chunks
— no file re-upload, no re-parse, no re-chunk, and document ids, chunk
ids, filenames and statuses all stay exactly as they are.

USAGE (from backend/, venv activated)
─────────────────────────────────────
    # Report only — makes no changes at all:
    python scripts/backfill_embeddings.py

    # Regenerate the missing embeddings:
    python scripts/backfill_embeddings.py --fix

    # Limit to one tenant:
    python scripts/backfill_embeddings.py --fix --customer-id 6

Safe to re-run: it only ever inserts embeddings for chunks that have
none, and never touches a chunk that already has one.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app.database.session import SessionLocal  # noqa: E402
from app.models.embedding import Embedding  # noqa: E402
from app.services.embedding_service import (  # noqa: E402
    embedding_service,
    EMBEDDING_MODEL
)
from app.services.cache_service import cache_service  # noqa: E402
from app.repositories.semantic_cache_repository import (  # noqa: E402
    SemanticCacheRepository
)


# Matches EmbeddingService.BATCH_SIZE — one OpenAI call per 100 chunks.
BATCH_SIZE = 100


def report(db, customer_id=None):
    """
    Per-tenant breakdown of chunks vs embeddings. This is the query that
    tells you whether `vector_chunks: 0` is a data problem (chunks with
    no embeddings) or something else entirely.
    """
    where = "WHERE dc.customer_id = :cid" if customer_id else ""

    rows = db.execute(
        text(f"""
            SELECT
                dc.customer_id,
                COUNT(DISTINCT dc.document_id)                    AS documents,
                COUNT(dc.id)                                      AS chunks,
                COUNT(e.id)                                       AS embeddings,
                COUNT(dc.id) - COUNT(e.id)                        AS missing
            FROM document_chunks dc
            LEFT JOIN embeddings e ON e.chunk_id = dc.id
            {where}
            GROUP BY dc.customer_id
            ORDER BY dc.customer_id
        """),
        {"cid": customer_id} if customer_id else {}
    ).fetchall()

    print()
    print(f"  {'customer_id':>12} {'documents':>10} {'chunks':>8} {'embeddings':>11} {'MISSING':>9}")
    print("  " + "-" * 54)

    total_missing = 0

    for r in rows:
        total_missing += r[4]
        flag = "  <-- vector search is dead for this tenant" if r[4] == r[2] and r[2] > 0 else ""
        print(f"  {r[0]:>12} {r[1]:>10} {r[2]:>8} {r[3]:>11} {r[4]:>9}{flag}")

    if not rows:
        print("  (no chunks found at all — nothing has been indexed yet)")

    # A NULL vector is a different failure from a missing row: it means
    # the embedding row was inserted but the vector never landed.
    null_vectors = db.execute(
        text("SELECT COUNT(*) FROM embeddings WHERE embedding IS NULL")
    ).scalar() or 0

    print()
    print(f"  embedding rows present but with a NULL vector : {null_vectors}")
    print(f"  chunks with no embedding row at all           : {total_missing}")

    if total_missing:
        docs = db.execute(
            text(f"""
                SELECT DISTINCT d.id, d.filename, d.status, d.customer_id
                FROM document_chunks dc
                JOIN documents d ON d.id = dc.document_id
                LEFT JOIN embeddings e ON e.chunk_id = dc.id
                WHERE e.id IS NULL {"AND dc.customer_id = :cid" if customer_id else ""}
                ORDER BY d.customer_id, d.id
            """),
            {"cid": customer_id} if customer_id else {}
        ).fetchall()

        print()
        print("  Affected documents (chunk text intact, vectors missing):")
        for d in docs:
            print(f"    customer={d[3]}  doc_id={d[0]}  status={d[2]:<10} {d[1]}")

    return total_missing


def backfill(db, customer_id=None):
    """
    Regenerates embeddings for every chunk that lacks one, in batches,
    reusing the same EmbeddingService the upload pipeline uses — so the
    vectors produced here are identical in model and dimensionality to
    the ones a fresh upload would create.
    """
    rows = db.execute(
        text(f"""
            SELECT dc.id, dc.customer_id, dc.chunk_text
            FROM document_chunks dc
            LEFT JOIN embeddings e ON e.chunk_id = dc.id
            WHERE e.id IS NULL
              AND dc.chunk_text IS NOT NULL
              AND length(trim(dc.chunk_text)) > 0
              {"AND dc.customer_id = :cid" if customer_id else ""}
            ORDER BY dc.id
        """),
        {"cid": customer_id} if customer_id else {}
    ).fetchall()

    if not rows:
        print("\n  Nothing to backfill — every chunk already has an embedding.")
        return set()

    print(f"\n  Backfilling {len(rows)} chunk(s) using {EMBEDDING_MODEL} ...")

    touched_customers = set()
    done = 0

    for start in range(0, len(rows), BATCH_SIZE):
        batch = rows[start:start + BATCH_SIZE]

        vectors = embedding_service.generate_embeddings_batch(
            [r[2] for r in batch]
        )

        db.add_all([
            Embedding(chunk_id=r[0], customer_id=r[1], embedding=vector)
            for r, vector in zip(batch, vectors)
        ])
        db.commit()

        for r in batch:
            touched_customers.add(r[1])

        done += len(batch)
        print(f"    {done}/{len(rows)} chunks embedded")

    return touched_customers


def main():
    parser = argparse.ArgumentParser(
        description="Diagnose/repair chunks that have no embedding row."
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Actually generate the missing embeddings (costs OpenAI tokens)."
    )
    parser.add_argument(
        "--customer-id",
        type=int,
        default=None,
        help="Restrict to a single tenant."
    )
    args = parser.parse_args()

    db = SessionLocal()

    try:
        print("\n=== BEFORE ===")
        missing = report(db, args.customer_id)

        if not args.fix:
            if missing:
                print(
                    "\n  Run again with --fix to regenerate these embeddings "
                    "from the existing chunk text (no re-upload needed)."
                )
            return

        if not missing:
            return

        touched = backfill(db, args.customer_id)

        # The cached answers were produced by keyword-only retrieval and
        # are now stale — with vector search restored, the same questions
        # can legitimately return better answers. Clearing both tiers
        # forces a fresh retrieval on the next ask.
        for cid in touched:
            SemanticCacheRepository(db).delete_by_customer(cid)
            cache_service.clear_for_customer(cid)
            print(f"  Cleared exact + semantic cache for customer_id={cid}")

        print("\n=== AFTER ===")
        report(db, args.customer_id)

    finally:
        db.close()


if __name__ == "__main__":
    main()
