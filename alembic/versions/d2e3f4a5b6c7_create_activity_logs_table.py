"""create activity_logs table

Frontend Activity Tracking (Module 2).

A separate table AND a separate migration from audit_logs, so the two
modules can be rolled back independently — activity tracking can be
removed without touching the security audit trail.

Hand-written for the same reason as the audit migration: autogenerate
does not round-trip this project's pgvector columns cleanly and would
emit changes against unrelated tables.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-08-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = 'd2e3f4a5b6c7'
down_revision: Union[str, Sequence[str], None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'activity_logs',

        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),

        # Server clock — authoritative. client_timestamp below is the
        # browser's, kept strictly separate and never used for ordering.
        sa.Column(
            'timestamp',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False
        ),

        sa.Column('user_id', sa.Integer(), nullable=True),
        sa.Column('user_email', sa.String(length=320), nullable=True),
        sa.Column('role', sa.String(length=32), nullable=True),
        sa.Column('customer_id', sa.Integer(), nullable=True),

        sa.Column('session_id', sa.String(length=128), nullable=True),

        sa.Column('activity_type', sa.String(length=64), nullable=False),
        sa.Column('page', sa.String(length=128), nullable=True),
        sa.Column('feature', sa.String(length=128), nullable=True),
        sa.Column('from_page', sa.String(length=128), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),

        # Named metadata_json because `metadata` is reserved on
        # SQLAlchemy's declarative Base.
        sa.Column('metadata_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),

        sa.Column('client_timestamp', sa.DateTime(timezone=True), nullable=True),

        sa.Column('request_id', sa.String(length=64), nullable=True),
        sa.Column('ip_address', sa.String(length=64), nullable=True),
        sa.Column('user_agent', sa.String(length=512), nullable=True),

        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False
        ),

        sa.PrimaryKeyConstraint('id'),

        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'],
            name='fk_activity_logs_user_id',
            ondelete='SET NULL'
        ),
        sa.ForeignKeyConstraint(
            ['customer_id'], ['customers.id'],
            name='fk_activity_logs_customer_id',
            ondelete='SET NULL'
        )
    )

    op.create_index(
        'ix_activity_logs_timestamp',
        'activity_logs',
        [sa.text('timestamp DESC')]
    )
    op.create_index(
        'ix_activity_logs_user_id_timestamp',
        'activity_logs',
        ['user_id', sa.text('timestamp DESC')]
    )

    # ASCENDING timestamp here, unlike every other index in this file:
    # session replay reads a session's events in the order they happened,
    # not newest-first.
    op.create_index(
        'ix_activity_logs_session_id_timestamp',
        'activity_logs',
        ['session_id', 'timestamp']
    )

    op.create_index(
        'ix_activity_logs_type_timestamp',
        'activity_logs',
        ['activity_type', sa.text('timestamp DESC')]
    )

    # Backs the "Time Spent Per Page" aggregate.
    op.create_index(
        'ix_activity_logs_page_timestamp',
        'activity_logs',
        ['page', sa.text('timestamp DESC')]
    )

    op.create_index(
        'ix_activity_logs_metadata',
        'activity_logs',
        ['metadata_json'],
        postgresql_using='gin'
    )


def downgrade() -> None:
    op.drop_index('ix_activity_logs_metadata', table_name='activity_logs')
    op.drop_index('ix_activity_logs_page_timestamp', table_name='activity_logs')
    op.drop_index('ix_activity_logs_type_timestamp', table_name='activity_logs')
    op.drop_index('ix_activity_logs_session_id_timestamp', table_name='activity_logs')
    op.drop_index('ix_activity_logs_user_id_timestamp', table_name='activity_logs')
    op.drop_index('ix_activity_logs_timestamp', table_name='activity_logs')
    op.drop_table('activity_logs')
