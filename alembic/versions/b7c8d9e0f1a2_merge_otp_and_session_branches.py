"""merge otp and session branches

Migration history had branched. `b2c3d4e5f6a7` (exact cache) ended up
with two children:

    b2c3d4e5f6a7 ─┬─ c1d2e3f4a5b6 → ... → a6b7c8d9e0f1   (audit / activity / sessions)
                  └─ 5d9549a52d89                          (otp table)

Two heads means `alembic upgrade head` fails outright with
"Multiple head revisions are present".

This is an empty merge revision — it has no upgrade/downgrade body and
touches no schema. Its only job is to give both branches a single common
descendant so there is one head again.

WHY A MERGE AND NOT A RE-POINT
──────────────────────────────
The obvious-looking fix is to edit 5d9549a52d89's down_revision to chain
it onto a6b7c8d9e0f1. That is wrong here: 5d9549a52d89 is very likely
already applied (the OTP feature is in use), and rewriting the ancestry
of an applied revision leaves alembic_version pointing into a history
that no longer exists. A merge revision is additive and safe whichever
branch each environment happens to be on.

Both branches are independent — the OTP table shares no columns or
constraints with audit_logs / activity_logs / user_sessions — so there
is nothing to reconcile.

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1, 5d9549a52d89
Create Date: 2026-08-06
"""
from typing import Sequence, Union

from alembic import op  # noqa: F401
import sqlalchemy as sa  # noqa: F401


revision: str = 'b7c8d9e0f1a2'

# A tuple, not a string — this is what makes it a merge point.
down_revision: Union[str, Sequence[str], None] = ('a6b7c8d9e0f1', '5d9549a52d89')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No schema change — this revision exists only to rejoin the branches."""
    pass


def downgrade() -> None:
    """No schema change to undo."""
    pass
