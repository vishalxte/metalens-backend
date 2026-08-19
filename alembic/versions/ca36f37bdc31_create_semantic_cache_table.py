"""create semantic cache table

Revision ID: ca36f37bdc31
Revises: ff69ac9ff84c
Create Date: 2026-07-01 11:42:07.036749

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector



# revision identifiers, used by Alembic.
revision: str = 'ca36f37bdc31'
down_revision: Union[str, Sequence[str], None] = 'ff69ac9ff84c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'semantic_cache',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('question_text', sa.Text(), nullable=False),
        sa.Column('question_embedding', Vector(384), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('sources', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Speeds up cosine similarity search once the table has meaningful rows.
    op.execute(
        "CREATE INDEX semantic_cache_embedding_idx "
        "ON semantic_cache USING ivfflat (question_embedding vector_cosine_ops) "
        "WITH (lists = 100)"
    )



def downgrade() -> None:
    op.drop_index('semantic_cache_embedding_idx', table_name='semantic_cache')
    op.drop_table('semantic_cache')
