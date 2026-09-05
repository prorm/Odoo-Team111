"""004_add_notes_table

Revision ID: 004_add_notes_table
Revises: 003_scaffolding_cleanup
Create Date: 2026-08-07 00:05:00.000000

Adds the `notes` table for the Note proof-of-concept entity — demonstrates
that AuditedEntity/BaseRepository/BaseService are actually reusable scaffolding
(the model only declares `content`; everything else — id, public_id, tenant_id,
created_at, updated_at, deleted_at, version — comes from the mixin).
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '004_add_notes_table'
down_revision: Union[str, None] = '003_scaffolding_cleanup'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notes',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('tenant_id', sa.String(length=64), nullable=False, server_default='default'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_notes_public_id', 'notes', ['public_id'], unique=True)
    op.create_index('ix_notes_tenant_id', 'notes', ['tenant_id'])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON notes TO harmonix360_app;")
    op.execute("GRANT USAGE, SELECT ON notes_id_seq TO harmonix360_app;")


def downgrade() -> None:
    op.drop_table('notes')
