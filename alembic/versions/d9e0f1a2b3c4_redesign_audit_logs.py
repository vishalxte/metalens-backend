"""redesign audit_logs: target_type/target_id, single event_time

21 columns -> 12.

DATA PRESERVED BY RENAME (not drop/re-add)
──────────────────────────────────────────
  timestamp    -> event_time
  resource     -> target_type
  resource_id  -> target_id

`resource`/`resource_id` already held exactly the target semantics the
new design formalises ("document":"42", "user":"35"), so renaming keeps
every existing row meaningful. Their values are normalised to upper case
below to match the new TargetType constants.

user_id, user_email, session_id, ip_address, category, action, status
and details are untouched.

DATA DROPPED
────────────
role, customer_id, request_id, user_agent, http_method, http_path,
http_status, duration_ms, created_at.

  - role / customer_id: derivable from user_id; this is a
    client-specific deployment, so customer_id was never a boundary.
  - request_id / http_*: request-level detail that belongs in the
    application log, which already records it per request.
  - duration_ms: only ever populated on AI and upload events, where the
    same timings are in details.
  - created_at: duplicated event_time to within milliseconds.

user_email and ip_address are deliberately KEPT — eight event types
(LOGIN_FAILED, OTP_FAILED, OTP_SEND_FAILED, AUTHENTICATION_FAILURE,
INACTIVE_ACCOUNT_ACCESS, INVALID_TOKEN, JWT_FAILURE, PERMISSION_DENIED)
have no user_id at all, because no user was ever resolved. Without these
two columns those rows would carry no identifying information whatsoever
and the SECURITY category would be non-functional.

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd9e0f1a2b3c4'
down_revision: Union[str, Sequence[str], None] = 'c8d9e0f1a2b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_INDEXES = (
    "ix_audit_logs_timestamp",
    "ix_audit_logs_category_timestamp",
    "ix_audit_logs_action_timestamp",
    "ix_audit_logs_user_id_timestamp",
    "ix_audit_logs_status_timestamp",
    "ix_audit_logs_request_id",
    "ix_audit_logs_resource",
    "ix_audit_logs_details",
)


def upgrade() -> None:
    # ── 1. Drop old indexes ──────────────────────────────────────────
    for name in OLD_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")

    # ── 2. Normalise existing data BEFORE renaming ───────────────────
    # Old rows used lower-case resource names ("document", "user",
    # "conversation", "session"); TargetType is upper case.
    op.execute("UPDATE audit_logs SET resource = upper(resource) WHERE resource IS NOT NULL")

    # Action renames to match the new constant set.
    op.execute(
        "UPDATE audit_logs SET action = 'USER_DELETED' WHERE action = 'USER_SOFT_DELETED'"
    )
    op.execute(
        "UPDATE audit_logs SET action = 'CUSTOMER_DELETED' "
        "WHERE action = 'CUSTOMER_SOFT_DELETED'"
    )

    # Customer events move out of USER_MANAGEMENT into their own
    # category, and gain the target the new design requires. The old
    # rows kept the customer id inside details, so it can be recovered
    # rather than lost.
    op.execute(
        """
        UPDATE audit_logs
        SET category = 'CUSTOMER_MANAGEMENT',
            resource = 'CUSTOMER',
            resource_id = COALESCE(resource_id, details->>'customer_id')
        WHERE action LIKE 'CUSTOMER\\_%'
        """
    )

    # DENIED was a third status; the new design has only SUCCESS /
    # FAILURE. A denied action did not succeed.
    op.execute("UPDATE audit_logs SET status = 'FAILURE' WHERE status = 'DENIED'")

    # ── 3. Renames (data preserved) ──────────────────────────────────
    op.alter_column('audit_logs', 'timestamp', new_column_name='event_time')
    op.alter_column('audit_logs', 'resource', new_column_name='target_type')
    op.alter_column('audit_logs', 'resource_id', new_column_name='target_id')

    # ── 4. Drop what the redesign removes ────────────────────────────
    for column in (
        'role', 'customer_id', 'request_id', 'user_agent',
        'http_method', 'http_path', 'http_status', 'duration_ms',
        'created_at'
    ):
        op.execute(f"ALTER TABLE audit_logs DROP COLUMN IF EXISTS {column}")

    # ── 5. Tighten widths to the new model ───────────────────────────
    op.alter_column('audit_logs', 'session_id',
                    type_=sa.String(length=64), existing_nullable=True)
    op.alter_column('audit_logs', 'category',
                    type_=sa.String(length=32), existing_nullable=False)
    op.alter_column('audit_logs', 'action',
                    type_=sa.String(length=64), existing_nullable=False)
    op.alter_column('audit_logs', 'target_type',
                    type_=sa.String(length=32), existing_nullable=True)
    op.alter_column('audit_logs', 'status',
                    type_=sa.String(length=16), existing_nullable=False)

    # ── 6. Rebuild indexes ───────────────────────────────────────────
    op.create_index('ix_audit_logs_event_time', 'audit_logs',
                    [sa.text('event_time DESC')])
    op.create_index('ix_audit_logs_user_id_event_time', 'audit_logs',
                    ['user_id', sa.text('event_time DESC')])
    op.create_index('ix_audit_logs_session_id_event_time', 'audit_logs',
                    ['session_id', sa.text('event_time DESC')])
    op.create_index('ix_audit_logs_category_event_time', 'audit_logs',
                    ['category', sa.text('event_time DESC')])
    op.create_index('ix_audit_logs_action_event_time', 'audit_logs',
                    ['action', sa.text('event_time DESC')])
    op.create_index('ix_audit_logs_target', 'audit_logs',
                    ['target_type', 'target_id'])


def downgrade() -> None:
    for name in (
        "ix_audit_logs_event_time",
        "ix_audit_logs_user_id_event_time",
        "ix_audit_logs_session_id_event_time",
        "ix_audit_logs_category_event_time",
        "ix_audit_logs_action_event_time",
        "ix_audit_logs_target",
    ):
        op.execute(f"DROP INDEX IF EXISTS {name}")

    # Re-added as nullable — dropped values cannot be recovered.
    op.add_column('audit_logs', sa.Column('role', sa.String(length=32), nullable=True))
    op.add_column('audit_logs', sa.Column('customer_id', sa.Integer(), nullable=True))
    op.add_column('audit_logs', sa.Column('request_id', sa.String(length=64), nullable=True))
    op.add_column('audit_logs', sa.Column('user_agent', sa.String(length=512), nullable=True))
    op.add_column('audit_logs', sa.Column('http_method', sa.String(length=10), nullable=True))
    op.add_column('audit_logs', sa.Column('http_path', sa.String(length=512), nullable=True))
    op.add_column('audit_logs', sa.Column('http_status', sa.Integer(), nullable=True))
    op.add_column('audit_logs', sa.Column('duration_ms', sa.Integer(), nullable=True))
    op.add_column('audit_logs', sa.Column(
        'created_at', sa.DateTime(timezone=True),
        server_default=sa.text('now()'), nullable=False))

    op.alter_column('audit_logs', 'target_id', new_column_name='resource_id')
    op.alter_column('audit_logs', 'target_type', new_column_name='resource')
    op.alter_column('audit_logs', 'event_time', new_column_name='timestamp')

    op.create_index('ix_audit_logs_timestamp', 'audit_logs', [sa.text('timestamp DESC')])
    op.create_index('ix_audit_logs_category_timestamp', 'audit_logs',
                    ['category', sa.text('timestamp DESC')])
    op.create_index('ix_audit_logs_action_timestamp', 'audit_logs',
                    ['action', sa.text('timestamp DESC')])
    op.create_index('ix_audit_logs_user_id_timestamp', 'audit_logs',
                    ['user_id', sa.text('timestamp DESC')])
    op.create_index('ix_audit_logs_status_timestamp', 'audit_logs',
                    ['status', sa.text('timestamp DESC')])
    op.create_index('ix_audit_logs_request_id', 'audit_logs', ['request_id'])
    op.create_index('ix_audit_logs_resource', 'audit_logs', ['resource', 'resource_id'])
    op.create_index('ix_audit_logs_details', 'audit_logs', ['details'],
                    postgresql_using='gin')
