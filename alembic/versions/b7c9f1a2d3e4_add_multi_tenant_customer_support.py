"""add multi-tenant customer support

Adds the `customers` table and wires customer_id (tenant scoping) through
users, documents, conversations, document_chunks, embeddings and
semantic_cache, per the multi-tenant SaaS redesign:

    Super Admin -> many Customers -> each Customer's own
    Documents / Conversations / Chunks / Embeddings / Semantic Cache.

Existing rows are backfilled into a bootstrap "Legacy Data" customer so
this migration is safe to run against a database that already has data
from the old single-tenant design, before the new customer_id columns are
made NOT NULL.

Revision ID: b7c9f1a2d3e4
Revises: 85fbde5d9615
Create Date: 2026-07-10 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c9f1a2d3e4'
down_revision: Union[str, Sequence[str], None] = '85fbde5d9615'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


LEGACY_CUSTOMER_EMAIL = "legacy-data@internal.local"


def upgrade() -> None:
    """Upgrade schema."""

    # ------------------------------------------------------------------
    # 1. customers table
    # ------------------------------------------------------------------
    op.create_table(
        'customers',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('company_name', sa.String(), nullable=False),
        sa.Column('address', sa.String(), nullable=True),
        sa.Column('mobile', sa.String(), nullable=True),
        sa.Column('email', sa.String(), nullable=False, unique=True),
        sa.Column('created_by', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )
    op.create_foreign_key(
        'fk_customers_created_by', 'customers', 'users', ['created_by'], ['id']
    )

    # ------------------------------------------------------------------
    # 2. users.role (replaces is_super_admin) + users.customer_id
    # ------------------------------------------------------------------
    op.add_column(
        'users',
        sa.Column('role', sa.String(), nullable=True)
    )
    op.add_column(
        'users',
        sa.Column('customer_id', sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        'fk_users_customer', 'users', 'customers', ['customer_id'], ['id']
    )

    # Backfill role from the old boolean before dropping it.
    op.execute(
        "UPDATE users SET role = 'SUPER_ADMIN' WHERE is_super_admin = true"
    )
    op.execute(
        "UPDATE users SET role = 'CUSTOMER' WHERE is_super_admin = false OR is_super_admin IS NULL"
    )
    op.alter_column('users', 'role', nullable=False, server_default='CUSTOMER')
    op.drop_column('users', 'is_super_admin')

    # ------------------------------------------------------------------
    # 3. Bootstrap a "Legacy Data" customer to own all pre-existing rows.
    #    created_by is whichever user looks like the original admin
    #    (falls back to the lowest user id if no super admin exists).
    # ------------------------------------------------------------------
    op.execute(
        f"""
        INSERT INTO customers (company_name, email, created_by, is_active, created_at, updated_at)
        SELECT
            'Legacy Data',
            '{LEGACY_CUSTOMER_EMAIL}',
            (
                SELECT id FROM users
                ORDER BY (role = 'SUPER_ADMIN') DESC, id ASC
                LIMIT 1
            ),
            true,
            now(),
            now()
        WHERE EXISTS (SELECT 1 FROM users)
        """
    )

    # Any pre-existing non-super-admin user gets attached to Legacy Data so
    # user.customer_id is never left NULL for a tenant user.
    op.execute(
        f"""
        UPDATE users
        SET customer_id = (SELECT id FROM customers WHERE email = '{LEGACY_CUSTOMER_EMAIL}')
        WHERE role != 'SUPER_ADMIN' AND customer_id IS NULL
        """
    )

    # ------------------------------------------------------------------
    # 4. documents: customer_id + created_by (replaces owner_id)
    # ------------------------------------------------------------------
    op.add_column('documents', sa.Column('customer_id', sa.Integer(), nullable=True))
    op.add_column('documents', sa.Column('created_by', sa.Integer(), nullable=True))

    op.execute("UPDATE documents SET created_by = owner_id WHERE owner_id IS NOT NULL")
    op.execute(
        f"""
        UPDATE documents
        SET customer_id = (SELECT id FROM customers WHERE email = '{LEGACY_CUSTOMER_EMAIL}')
        WHERE customer_id IS NULL
        """
    )

    op.drop_constraint('fk_documents_owner', 'documents', type_='foreignkey')
    op.drop_column('documents', 'owner_id')

    op.alter_column('documents', 'customer_id', nullable=False)
    op.create_foreign_key(
        'fk_documents_customer', 'documents', 'customers', ['customer_id'], ['id']
    )
    op.create_foreign_key(
        'fk_documents_created_by', 'documents', 'users', ['created_by'], ['id']
    )

    # ------------------------------------------------------------------
    # 5. conversations.customer_id (derived from the owning user's tenant)
    # ------------------------------------------------------------------
    op.add_column('conversations', sa.Column('customer_id', sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE conversations c
        SET customer_id = u.customer_id
        FROM users u
        WHERE u.id = c.owner_id AND u.customer_id IS NOT NULL
        """
    )
    op.execute(
        f"""
        UPDATE conversations
        SET customer_id = (SELECT id FROM customers WHERE email = '{LEGACY_CUSTOMER_EMAIL}')
        WHERE customer_id IS NULL
        """
    )
    op.alter_column('conversations', 'customer_id', nullable=False)
    op.create_foreign_key(
        'fk_conversations_customer', 'conversations', 'customers', ['customer_id'], ['id']
    )

    # ------------------------------------------------------------------
    # 6. document_chunks.customer_id (denormalized from documents)
    # ------------------------------------------------------------------
    op.add_column('document_chunks', sa.Column('customer_id', sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE document_chunks dc
        SET customer_id = d.customer_id
        FROM documents d
        WHERE d.id = dc.document_id
        """
    )
    op.alter_column('document_chunks', 'customer_id', nullable=False)
    op.create_foreign_key(
        'fk_chunks_customer', 'document_chunks', 'customers', ['customer_id'], ['id']
    )
    op.create_index(
        'ix_document_chunks_customer_id', 'document_chunks', ['customer_id']
    )

    # ------------------------------------------------------------------
    # 7. embeddings.customer_id (denormalized from document_chunks)
    # ------------------------------------------------------------------
    op.add_column('embeddings', sa.Column('customer_id', sa.Integer(), nullable=True))
    op.execute(
        """
        UPDATE embeddings e
        SET customer_id = dc.customer_id
        FROM document_chunks dc
        WHERE dc.id = e.chunk_id
        """
    )
    op.alter_column('embeddings', 'customer_id', nullable=False)
    op.create_foreign_key(
        'fk_embeddings_customer', 'embeddings', 'customers', ['customer_id'], ['id']
    )
    op.create_index(
        'ix_embeddings_customer_id', 'embeddings', ['customer_id']
    )

    # ------------------------------------------------------------------
    # 8. semantic_cache.customer_id
    #    Cached answers aren't meaningfully attributable to one tenant
    #    retroactively, and the cache is fully regenerable, so we clear
    #    existing entries rather than guessing an owner for them.
    # ------------------------------------------------------------------
    op.execute("DELETE FROM semantic_cache")
    op.add_column('semantic_cache', sa.Column('customer_id', sa.Integer(), nullable=True))
    op.alter_column('semantic_cache', 'customer_id', nullable=False)
    op.create_foreign_key(
        'fk_semantic_cache_customer', 'semantic_cache', 'customers', ['customer_id'], ['id']
    )
    op.create_index(
        'ix_semantic_cache_customer_id', 'semantic_cache', ['customer_id']
    )

    # ------------------------------------------------------------------
    # 9. Remaining indexes for fast tenant-scoped lookups.
    # ------------------------------------------------------------------
    op.create_index('ix_documents_customer_id', 'documents', ['customer_id'])
    op.create_index('ix_conversations_customer_id', 'conversations', ['customer_id'])
    op.create_index('ix_users_customer_id', 'users', ['customer_id'])


def downgrade() -> None:
    """Downgrade schema. Best-effort — cache data and the fine-grained
    per-tenant mapping created during upgrade() cannot be perfectly
    restored."""

    op.drop_index('ix_users_customer_id', table_name='users')
    op.drop_index('ix_conversations_customer_id', table_name='conversations')
    op.drop_index('ix_documents_customer_id', table_name='documents')

    op.drop_index('ix_semantic_cache_customer_id', table_name='semantic_cache')
    op.drop_constraint('fk_semantic_cache_customer', 'semantic_cache', type_='foreignkey')
    op.drop_column('semantic_cache', 'customer_id')

    op.drop_index('ix_embeddings_customer_id', table_name='embeddings')
    op.drop_constraint('fk_embeddings_customer', 'embeddings', type_='foreignkey')
    op.drop_column('embeddings', 'customer_id')

    op.drop_index('ix_document_chunks_customer_id', table_name='document_chunks')
    op.drop_constraint('fk_chunks_customer', 'document_chunks', type_='foreignkey')
    op.drop_column('document_chunks', 'customer_id')

    op.drop_constraint('fk_conversations_customer', 'conversations', type_='foreignkey')
    op.drop_column('conversations', 'customer_id')

    op.add_column('documents', sa.Column('owner_id', sa.Integer(), nullable=True))
    op.execute("UPDATE documents SET owner_id = created_by")
    op.create_foreign_key('fk_documents_owner', 'documents', 'users', ['owner_id'], ['id'])
    op.drop_constraint('fk_documents_created_by', 'documents', type_='foreignkey')
    op.drop_constraint('fk_documents_customer', 'documents', type_='foreignkey')
    op.drop_column('documents', 'created_by')
    op.drop_column('documents', 'customer_id')

    op.add_column('users', sa.Column('is_super_admin', sa.Boolean(), nullable=False, server_default=sa.text('false')))
    op.execute("UPDATE users SET is_super_admin = true WHERE role = 'SUPER_ADMIN'")
    op.drop_constraint('fk_users_customer', 'users', type_='foreignkey')
    op.drop_column('users', 'customer_id')
    op.drop_column('users', 'role')

    op.drop_constraint('fk_customers_created_by', 'customers', type_='foreignkey')
    op.drop_table('customers')
