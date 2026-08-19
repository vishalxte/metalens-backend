"""create user_sessions table

Login session lifecycle, keyed on the JWT `jti` claim.

Hand-written for the same reason as the audit/activity migrations:
autogenerate does not round-trip this project's pgvector columns cleanly
and would emit changes against unrelated tables.

Purely additive — one new table plus its indexes. No existing table,
column, constraint or index is read, altered or dropped.

Deployment note: this table starts empty, and every token already in
circulation was issued without a `jti`. SessionService fails open on a
missing `jti` and on a missing row, so nobody is signed out by running
this migration. Sessions begin being recorded at the next login.

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-08-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, Sequence[str], None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_sessions',

        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),

        # The JWT `jti`. UNIQUE because one jti identifies exactly one
        # login — and that uniqueness is also what makes the
        # once-per-request revocation lookup a single index hit.
        sa.Column('session_id', sa.String(length=64), nullable=False),

        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('user_email', sa.String(length=320), nullable=True),
        sa.Column('role', sa.String(length=32), nullable=True),
        sa.Column('customer_id', sa.Integer(), nullable=True),

        sa.Column(
            'started_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False
        ),
        sa.Column(
            'last_seen_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False
        ),

        # Mirrors the token's `exp`, so "who is logged in right now" can
        # be answered from this table alone with no token decoding.
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),

        # NULL while live. Any non-NULL value means the token must be
        # rejected even though it is still cryptographically valid —
        # this column IS the revocation mechanism.
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('end_reason', sa.String(length=32), nullable=True),
        sa.Column('revoked_by_user_id', sa.Integer(), nullable=True),

        sa.Column('ip_address', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=512), nullable=True),

        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False
        ),

        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id', name='uq_user_sessions_session_id'),

        # SET NULL, never CASCADE — deleting a user must not erase the
        # record that they were logged in. user_email preserves identity.
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'],
            name='fk_user_sessions_user_id',
            ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['customer_id'], ['customers.id'],
            name='fk_user_sessions_customer_id',
            ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['revoked_by_user_id'], ['users.id'],
            name='fk_user_sessions_revoked_by',
            ondelete='SET NULL'
        )
    )

    # Hot path: the per-request revocation lookup.
    op.create_index(
        'ix_user_sessions_session_id',
        'user_sessions',
        ['session_id']
    )

    # PARTIAL index matching the exact predicate the active-sessions
    # query uses (ended_at IS NULL AND expires_at > now()). Keeps the
    # "who is online" screen off every historical closed session, which
    # is the bulk of the table after a few weeks.
    op.create_index(
        'ix_user_sessions_active',
        'user_sessions',
        ['expires_at'],
        postgresql_where=sa.text('ended_at IS NULL')
    )

    op.create_index(
        'ix_user_sessions_user_id_started',
        'user_sessions',
        ['user_id', sa.text('started_at DESC')]
    )
    op.create_index(
        'ix_user_sessions_customer_id',
        'user_sessions',
        ['customer_id']
    )
    op.create_index(
        'ix_user_sessions_started_at',
        'user_sessions',
        [sa.text('started_at DESC')]
    )


def downgrade() -> None:
    op.drop_index('ix_user_sessions_started_at', table_name='user_sessions')
    op.drop_index('ix_user_sessions_customer_id', table_name='user_sessions')
    op.drop_index('ix_user_sessions_user_id_started', table_name='user_sessions')
    op.drop_index('ix_user_sessions_active', table_name='user_sessions')
    op.drop_index('ix_user_sessions_session_id', table_name='user_sessions')
    op.drop_table('user_sessions')
