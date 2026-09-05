"""003_scaffolding_cleanup

Revision ID: 003_scaffolding_cleanup
Revises: 002_week2_transfer_ai
Create Date: 2026-08-07 00:00:00.000000

Two independent fixes surfaced by the base-scaffolding audit:

1. Drop `audit_events` — an unused duplicate of `audit_logs`. Nothing in the
   codebase ever wrote to or read from it; `AuditLogger`/`AuditQueryRepository`
   only ever touch `audit_logs`. Removed rather than kept as dead schema.

2. Namespace public_id by entity type. AssetRepository, TransferRepository and
   BookingRepository all encoded public_id with the SAME global Hashids salt,
   so Asset id=5 and TransferRequest id=5 hashed to the identical string.
   Passing one entity's public_id where another's was expected (e.g. a
   TransferRequest id into an asset_public_id field) would silently decode
   and resolve against the wrong table whenever both had a row at that id.
   Existing rows are re-encoded in place with an "ast_"/"trf_"/"bkg_" prefix
   (same salt, same underlying integer id — just namespaced), rather than
   invalidated, even though this is pre-production data where invalidating
   would also have been an acceptable outcome.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from hashids import Hashids
from app.core.config import settings

revision: str = '003_scaffolding_cleanup'
down_revision: Union[str, None] = '002_week2_transfer_ai'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_hashids = Hashids(salt=settings.HASHID_SALT, min_length=8)

_NAMESPACED_TABLES = {
    "assets": "ast",
    "transfer_requests": "trf",
    "resource_bookings": "bkg",
}


def upgrade() -> None:
    op.drop_table('audit_events')

    bind = op.get_bind()
    for table, prefix in _NAMESPACED_TABLES.items():
        rows = bind.execute(sa.text(f"SELECT id, public_id FROM {table}")).fetchall()
        for row in rows:
            if row.public_id and row.public_id.startswith(f"{prefix}_"):
                continue  # already namespaced
            new_public_id = f"{prefix}_{_hashids.encode(row.id)}"
            bind.execute(
                sa.text(f"UPDATE {table} SET public_id = :pid WHERE id = :id"),
                {"pid": new_public_id, "id": row.id},
            )


def downgrade() -> None:
    bind = op.get_bind()
    for table, prefix in _NAMESPACED_TABLES.items():
        rows = bind.execute(sa.text(f"SELECT id, public_id FROM {table}")).fetchall()
        for row in rows:
            if row.public_id and row.public_id.startswith(f"{prefix}_"):
                old_public_id = row.public_id[len(prefix) + 1:]
                bind.execute(
                    sa.text(f"UPDATE {table} SET public_id = :pid WHERE id = :id"),
                    {"pid": old_public_id, "id": row.id},
                )

    op.create_table(
        'audit_events',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('entity', sa.String(length=128), nullable=False),
        sa.Column('entity_id', sa.String(length=128), nullable=False),
        sa.Column('action', sa.String(length=128), nullable=False),
        sa.Column('before', sa.JSON(), nullable=True),
        sa.Column('after', sa.JSON(), nullable=True),
        sa.Column('actor_type', sa.String(length=32), nullable=False, server_default='HUMAN'),
        sa.Column('actor_id', sa.String(length=128), nullable=True),
        sa.Column('tenant_id', sa.String(length=64), nullable=True),
        sa.Column('request_id', sa.String(length=128), nullable=True),
        sa.Column('correlation_id', sa.String(length=128), nullable=True),
        sa.Column('trace_id', sa.String(length=128), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id')
    )
