"""switch embeddings to openai text-embedding-3-small (1536 dims)

Revision ID: a1b2c3d4e5f6
Revises: b7c9f1a2d3e4
Create Date: 2026-07-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'b7c9f1a2d3e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Switching the embedding model from all-MiniLM-L6-v2 (384 dims) to
    OpenAI text-embedding-3-small (1536 dims) means every previously
    stored vector is the wrong shape for the new column type and cannot
    be reused. Both vector-bearing tables are cleared before the column
    type is changed, since old 384-dim vectors are meaningless once the
    embedding model changes.

    After this migration runs, all documents must be re-uploaded /
    re-indexed so their chunks get fresh 1536-dim embeddings — existing
    document rows and chunk text are left intact, only the embeddings
    themselves and the semantic cache are cleared.
    """

    # --- embeddings.embedding: 384 -> 1536 ---
    op.execute("TRUNCATE TABLE embeddings")
    op.drop_column('embeddings', 'embedding')
    op.add_column(
        'embeddings',
        sa.Column('embedding', Vector(1536), nullable=True)
    )

    # --- semantic_cache.question_embedding: 384 -> 1536 ---
    # Use "IF EXISTS" instead of op.drop_index(): on this database the
    # index was never actually created (or was already dropped by hand),
    # so an unconditional drop_index() errors with UndefinedObject.
    op.execute("DROP INDEX IF EXISTS semantic_cache_embedding_idx")
    op.execute("TRUNCATE TABLE semantic_cache")
    op.drop_column('semantic_cache', 'question_embedding')
    op.add_column(
        'semantic_cache',
        sa.Column('question_embedding', Vector(1536), nullable=False)
    )
    op.execute(
        "CREATE INDEX semantic_cache_embedding_idx "
        "ON semantic_cache USING ivfflat (question_embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS semantic_cache_embedding_idx")
    op.execute("TRUNCATE TABLE semantic_cache")
    op.drop_column('semantic_cache', 'question_embedding')
    op.add_column(
        'semantic_cache',
        sa.Column('question_embedding', Vector(384), nullable=False)
    )
    op.execute(
        "CREATE INDEX semantic_cache_embedding_idx "
        "ON semantic_cache USING ivfflat (question_embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )

    op.execute("TRUNCATE TABLE embeddings")
    op.drop_column('embeddings', 'embedding')
    op.add_column(
        'embeddings',
        sa.Column('embedding', Vector(384), nullable=True)
    )
