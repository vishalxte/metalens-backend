"""create exact cache table (Postgres replacement for Redis exact-match cache)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'exact_cache',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cache_key', sa.String(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('response', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['customer_id'], ['customers.id']),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_index('ix_exact_cache_cache_key', 'exact_cache', ['cache_key'], unique=True)
    op.create_index('ix_exact_cache_customer_id', 'exact_cache', ['customer_id'], unique=False)
    op.create_index('ix_exact_cache_expires_at', 'exact_cache', ['expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_exact_cache_expires_at', table_name='exact_cache')
    op.drop_index('ix_exact_cache_customer_id', table_name='exact_cache')
    op.drop_index('ix_exact_cache_cache_key', table_name='exact_cache')
    op.drop_table('exact_cache')
