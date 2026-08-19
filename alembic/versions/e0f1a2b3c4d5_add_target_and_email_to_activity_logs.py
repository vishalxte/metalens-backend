"""add user_email, target_type, target_id to activity_logs

activity_logs goes from 9 columns to 12, bringing it in line with
audit_logs on the two questions both tables need to answer:

    WHO did it   -> user_id + user_email
    WHAT to      -> target_type + target_id

`user_email` was removed in the c8d9e0f1a2b3 redesign and is being
reinstated deliberately. It is denormalized on purpose: `user_id` has
ON DELETE SET NULL, so once a user is deleted their activity rows lose
all identity unless the email is kept alongside. It also lets the log be
read without joining `users`.

`target_type` / `target_id` distinguish "the user clicked delete" from
"the user deleted document 42". `feature` already said WHAT action;
these say WHICH ROW.

All three are nullable, so this is safe on the existing table and needs
no backfill:

  - existing rows keep user_email NULL — the value was dropped by the
    earlier migration and cannot be recovered. New rows populate it.
  - target_* is NULL for PAGE_VIEW / NAVIGATION / QUESTION_SUBMITTED by
    design (there is nothing being acted upon), and for UPLOAD_DOCUMENT
    / CREATE_USER, which are recorded at click time before the row they
    create exists.

target_id is VARCHAR, matching audit_logs.target_id, so filenames and
UUIDs fit as easily as numeric ids.

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-08-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e0f1a2b3c4d5'
down_revision: Union[str, Sequence[str], None] = 'd9e0f1a2b3c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'activity_logs',
        sa.Column('user_email', sa.String(length=320), nullable=True)
    )
    op.add_column(
        'activity_logs',
        sa.Column('target_type', sa.String(length=32), nullable=True)
    )
    op.add_column(
        'activity_logs',
        sa.Column('target_id', sa.String(length=128), nullable=True)
    )

    # "everything that happened to document 42" — same shape as the
    # equivalent index on audit_logs, so the two tables answer that
    # question the same way.
    op.create_index(
        'ix_activity_logs_target',
        'activity_logs',
        ['target_type', 'target_id']
    )


def downgrade() -> None:
    op.drop_index('ix_activity_logs_target', table_name='activity_logs')
    op.drop_column('activity_logs', 'target_id')
    op.drop_column('activity_logs', 'target_type')
    op.drop_column('activity_logs', 'user_email')
