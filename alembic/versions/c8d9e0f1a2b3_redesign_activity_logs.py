"""redesign activity_logs: minimal behavioural schema

18 columns -> 9. The table now answers exactly one question — "what did
the user do in the UI?" — and carries nothing else.

DATA PRESERVED
──────────────
`timestamp` is RENAMED to `event_time` (ALTER TABLE ... RENAME COLUMN),
not dropped and re-added, so every existing row keeps its time.
`activity_type` is likewise renamed to `action`. user_id, session_id,
page, from_page and feature are untouched.

DATA DROPPED — and why it is acceptable here
────────────────────────────────────────────
user_email, role, customer_id, duration_ms, metadata_json,
client_timestamp, request_id, ip_address, user_agent, created_at.

There is no lossless place to move these: the redesign's whole point is
that activity_logs should not carry technical/network attribution. The
equivalent information for any authenticated request still exists in
audit_logs (user_email, ip_address) and user_sessions (device, IP,
login time), joinable on session_id. Current table volume is
development data only.

ROWS REMAPPED, NOT DELETED
──────────────────────────
The old table allowed SESSION_START / SESSION_END / FEATURE_USAGE.
Those actions no longer exist:
  - FEATURE_USAGE -> CLICK   (same meaning: a named feature was used,
                              and `feature` already holds which one)
  - SESSION_START / SESSION_END -> deleted. They recorded session
    boundaries, which user_sessions now records properly with
    login_at / ended_at / ended_reason. Keeping them would be a second,
    less reliable copy of the same fact.

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c8d9e0f1a2b3'
down_revision: Union[str, Sequence[str], None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_INDEXES = (
    "ix_activity_logs_timestamp",
    "ix_activity_logs_user_id_timestamp",
    "ix_activity_logs_session_id_timestamp",
    "ix_activity_logs_type_timestamp",
    "ix_activity_logs_page_timestamp",
    "ix_activity_logs_metadata",
)


def upgrade() -> None:
    # ── 1. Drop old indexes first ────────────────────────────────────
    # They reference columns that are about to be renamed or dropped.
    # IF EXISTS because a partially-migrated environment may not have
    # all of them.
    for name in OLD_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")

    # ── 2. Remap rows whose action no longer exists ──────────────────
    # Done BEFORE the rename so the column is still called
    # activity_type.
    op.execute(
        "UPDATE activity_logs SET activity_type = 'CLICK' "
        "WHERE activity_type = 'FEATURE_USAGE'"
    )
    op.execute(
        "DELETE FROM activity_logs "
        "WHERE activity_type IN ('SESSION_START', 'SESSION_END')"
    )

    # ── 3. Renames (data preserved) ──────────────────────────────────
    op.alter_column('activity_logs', 'timestamp', new_column_name='event_time')
    op.alter_column('activity_logs', 'activity_type', new_column_name='action')

    # ── 4. New column ────────────────────────────────────────────────
    op.add_column('activity_logs', sa.Column('question_text', sa.Text(), nullable=True))

    # ── 5. Drop everything the redesign removes ──────────────────────
    for column in (
        'user_email', 'role', 'customer_id', 'duration_ms',
        'metadata_json', 'client_timestamp', 'request_id',
        'ip_address', 'user_agent', 'created_at'
    ):
        op.execute(f"ALTER TABLE activity_logs DROP COLUMN IF EXISTS {column}")

    # ── 6. Tighten remaining column widths to the new model ──────────
    op.alter_column('activity_logs', 'action',
                    type_=sa.String(length=32), existing_nullable=False)
    op.alter_column('activity_logs', 'page',
                    type_=sa.String(length=64), existing_nullable=True)
    op.alter_column('activity_logs', 'from_page',
                    type_=sa.String(length=64), existing_nullable=True)
    op.alter_column('activity_logs', 'feature',
                    type_=sa.String(length=64), existing_nullable=True)

    # ── 7. Rebuild indexes on the new names ──────────────────────────
    op.create_index('ix_activity_logs_event_time', 'activity_logs',
                    [sa.text('event_time DESC')])
    op.create_index('ix_activity_logs_user_id_event_time', 'activity_logs',
                    ['user_id', sa.text('event_time DESC')])
    op.create_index('ix_activity_logs_session_id_event_time', 'activity_logs',
                    ['session_id', 'event_time'])
    op.create_index('ix_activity_logs_action_event_time', 'activity_logs',
                    ['action', sa.text('event_time DESC')])


def downgrade() -> None:
    for name in (
        "ix_activity_logs_event_time",
        "ix_activity_logs_user_id_event_time",
        "ix_activity_logs_session_id_event_time",
        "ix_activity_logs_action_event_time",
    ):
        op.execute(f"DROP INDEX IF EXISTS {name}")

    op.drop_column('activity_logs', 'question_text')

    # Re-added as nullable — the dropped values cannot be recovered.
    op.add_column('activity_logs', sa.Column('user_email', sa.String(length=320), nullable=True))
    op.add_column('activity_logs', sa.Column('role', sa.String(length=32), nullable=True))
    op.add_column('activity_logs', sa.Column('customer_id', sa.Integer(), nullable=True))
    op.add_column('activity_logs', sa.Column('duration_ms', sa.Integer(), nullable=True))
    op.add_column('activity_logs', sa.Column('metadata_json', sa.dialects.postgresql.JSONB(), nullable=True))
    op.add_column('activity_logs', sa.Column('client_timestamp', sa.DateTime(timezone=True), nullable=True))
    op.add_column('activity_logs', sa.Column('request_id', sa.String(length=64), nullable=True))
    op.add_column('activity_logs', sa.Column('ip_address', sa.String(length=64), nullable=True))
    op.add_column('activity_logs', sa.Column('user_agent', sa.String(length=512), nullable=True))
    op.add_column('activity_logs', sa.Column(
        'created_at', sa.DateTime(timezone=True),
        server_default=sa.text('now()'), nullable=False))

    op.alter_column('activity_logs', 'action', new_column_name='activity_type')
    op.alter_column('activity_logs', 'event_time', new_column_name='timestamp')

    op.create_index('ix_activity_logs_timestamp', 'activity_logs',
                    [sa.text('timestamp DESC')])
    op.create_index('ix_activity_logs_user_id_timestamp', 'activity_logs',
                    ['user_id', sa.text('timestamp DESC')])
    op.create_index('ix_activity_logs_session_id_timestamp', 'activity_logs',
                    ['session_id', 'timestamp'])
    op.create_index('ix_activity_logs_type_timestamp', 'activity_logs',
                    ['activity_type', sa.text('timestamp DESC')])
    op.create_index('ix_activity_logs_page_timestamp', 'activity_logs',
                    ['page', sa.text('timestamp DESC')])
