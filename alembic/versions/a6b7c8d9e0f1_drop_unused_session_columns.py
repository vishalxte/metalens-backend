"""drop unused user_sessions columns: country, city, refresh_jti

These three were added speculatively in f4a5b6c7d8e9 and nothing ever
wrote to them:

  country / city  needed a GeoIP source (MaxMind database or a hosted
                  lookup) that is a deployment decision, not a code one.
                  Neither was configured, so both stayed NULL on every row.

  refresh_jti     reserved for refresh tokens, which do not exist yet.

An always-NULL column is worse than no column: it implies data is being
collected when it is not, it shows up empty in every export and admin
screen, and it invites someone to write a query that silently returns
nothing. Adding a column back later is one ADD COLUMN — cheap enough
that carrying dead columns "just in case" is not worth it.

A SEPARATE migration rather than an edit to f4a5b6c7d8e9, because that
revision is already applied (the columns exist in the deployed database).

The downgrade recreates all three as nullable, so this is fully
reversible. No data can be lost — there is none to lose.

Revision ID: a6b7c8d9e0f1
Revises: f4a5b6c7d8e9
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a6b7c8d9e0f1'
down_revision: Union[str, Sequence[str], None] = 'f4a5b6c7d8e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('user_sessions', 'refresh_jti')
    op.drop_column('user_sessions', 'city')
    op.drop_column('user_sessions', 'country')


def downgrade() -> None:
    op.add_column('user_sessions', sa.Column('country', sa.String(length=64), nullable=True))
    op.add_column('user_sessions', sa.Column('city', sa.String(length=128), nullable=True))
    op.add_column('user_sessions', sa.Column('refresh_jti', sa.String(length=64), nullable=True))
