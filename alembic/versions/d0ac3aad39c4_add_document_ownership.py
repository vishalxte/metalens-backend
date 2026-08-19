"""add document ownership

Revision ID: d0ac3aad39c4
Revises: 21f89e8613d1
Create Date: 2026-06-22 15:32:11.247941

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd0ac3aad39c4'
down_revision: Union[str, Sequence[str], None] = '21f89e8613d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.add_column(
        'documents',
        sa.Column(
            'status',
            sa.String(),
            nullable=False,
            server_default='PENDING'
        )
    )

    op.add_column(
        'documents',
        sa.Column(
            'owner_id',
            sa.Integer(),
            nullable=True
        )
    )

    op.create_foreign_key(
        'fk_documents_owner',
        'documents',
        'users',
        ['owner_id'],
        ['id']
    )

    op.alter_column(
        'documents',
        'status',
        server_default=None
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_constraint(
        'fk_documents_owner',
        'documents',
        type_='foreignkey'
    )

    op.drop_column(
        'documents',
        'owner_id'
    )

    op.drop_column(
        'documents',
        'status'
    )