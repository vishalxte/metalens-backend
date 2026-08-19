"""store semantic_cache.sources as TEXT for application-level encryption

Revision ID: f4a6b8c0d2e4
Revises: e3f4a5b6c7d8
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f4a6b8c0d2e4"
down_revision: Union[str, Sequence[str], None] = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Preserve existing JSON data as its JSON text representation. The
    # separate encryption-data migration then encrypts these TEXT values.
    op.alter_column(
        "semantic_cache",
        "sources",
        existing_type=sa.JSON(),
        type_=sa.Text(),
        postgresql_using="sources::text",
        existing_nullable=False,
    )


def downgrade() -> None:
    # This downgrade is valid only after encrypted data has been removed or
    # decrypted externally; PostgreSQL cannot cast ciphertext to JSON.
    op.alter_column(
        "semantic_cache",
        "sources",
        existing_type=sa.Text(),
        type_=sa.JSON(),
        postgresql_using="sources::json",
        existing_nullable=False,
    )
