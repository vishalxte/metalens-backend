"""extend user_sessions: spec naming + device/geo/refresh columns

A SEPARATE migration rather than an edit to e3f4a5b6c7d8, because that
revision may already have been applied. Editing an applied migration
leaves the database and the migration history disagreeing, and anyone
who already ran it would never pick the change up.

Two things happen here:

  1. RENAMES to match the agreed schema naming:
        started_at  -> login_at
        end_reason  -> ended_reason
     op.alter_column(new_column_name=...) emits ALTER TABLE ... RENAME
     COLUMN, which preserves the data and the column's position — it is
     not a drop/add, so nothing is lost even if sessions already exist.

  2. NEW COLUMNS, all nullable, so the change is safe on a populated
     table and needs no backfill:
        browser / platform / device_name  - derived from user_agent at
                                            login (see app/core/user_agent.py)
        country / city                    - GeoIP, populated only when a
                                            source is configured
        refresh_jti                       - reserved for refresh tokens
        updated_at                        - row-level change stamp

Existing rows keep working: the derived columns stay NULL for sessions
created before this migration, and every consumer treats them as
optional.

Revision ID: f4a5b6c7d8e9
Revises: e3f4a5b6c7d8
Create Date: 2026-08-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f4a5b6c7d8e9'
down_revision: Union[str, Sequence[str], None] = 'e3f4a5b6c7d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Renames ───────────────────────────────────────────────────
    op.alter_column(
        'user_sessions',
        'started_at',
        new_column_name='login_at'
    )
    op.alter_column(
        'user_sessions',
        'end_reason',
        new_column_name='ended_reason'
    )

    # The two indexes built on the old column name are rebuilt so their
    # names stay honest about what they cover.
    op.drop_index('ix_user_sessions_user_id_started', table_name='user_sessions')
    op.drop_index('ix_user_sessions_started_at', table_name='user_sessions')

    op.create_index(
        'ix_user_sessions_user_id_login_at',
        'user_sessions',
        ['user_id', sa.text('login_at DESC')]
    )
    op.create_index(
        'ix_user_sessions_login_at',
        'user_sessions',
        [sa.text('login_at DESC')]
    )

    # ── 2. Device attribution (derived from user_agent at login) ─────
    op.add_column('user_sessions', sa.Column('browser', sa.String(length=64), nullable=True))
    op.add_column('user_sessions', sa.Column('platform', sa.String(length=64), nullable=True))
    op.add_column('user_sessions', sa.Column('device_name', sa.String(length=128), nullable=True))

    # ── 3. Geo enrichment (columns only — no GeoIP source bundled) ───
    op.add_column('user_sessions', sa.Column('country', sa.String(length=64), nullable=True))
    op.add_column('user_sessions', sa.Column('city', sa.String(length=128), nullable=True))

    # ── 4. Reserved for refresh tokens ───────────────────────────────
    op.add_column('user_sessions', sa.Column('refresh_jti', sa.String(length=64), nullable=True))

    # ── 5. Row change stamp ──────────────────────────────────────────
    # server_default now() so existing rows get a sensible value rather
    # than NULL; the model also sets it via onupdate.
    op.add_column(
        'user_sessions',
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False
        )
    )


def downgrade() -> None:
    op.drop_column('user_sessions', 'updated_at')
    op.drop_column('user_sessions', 'refresh_jti')
    op.drop_column('user_sessions', 'city')
    op.drop_column('user_sessions', 'country')
    op.drop_column('user_sessions', 'device_name')
    op.drop_column('user_sessions', 'platform')
    op.drop_column('user_sessions', 'browser')

    op.drop_index('ix_user_sessions_login_at', table_name='user_sessions')
    op.drop_index('ix_user_sessions_user_id_login_at', table_name='user_sessions')

    op.create_index(
        'ix_user_sessions_user_id_started',
        'user_sessions',
        ['user_id', sa.text('started_at DESC')]
    )
    op.create_index(
        'ix_user_sessions_started_at',
        'user_sessions',
        [sa.text('started_at DESC')]
    )

    op.alter_column('user_sessions', 'ended_reason', new_column_name='end_reason')
    op.alter_column('user_sessions', 'login_at', new_column_name='started_at')
