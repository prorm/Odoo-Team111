"""014_sync_mutations_table

Revision ID: 014_sync_mutations_table
Revises: 013_add_optimistic_version
Create Date: 2026-09-03 09:15:00.000000

Idempotency log for POST /sync/push (app/services/sync.py, app/models/entities.py
SyncMutation). See §13.3: deduplication is per `client_mutation_id`, checked
inside the same DB transaction that applies the mutation, deliberately not
layered only on top of the existing Idempotency-Key header
(app/middleware/idempotency.py) — that middleware caches one response per
header value for a whole batch, which cannot distinguish "replay this exact
op" from "this op happens to be in a batch I've seen a header for before".

`created_at` uses the same server-side, session-timezone-immune expression as
app/core/clock.py's server_utc_now() (raw SQL is allowed in migrations per
Section 0 of HARMONIX360_ARCHITECTURE.md) rather than a Python-computed default,
for the same cross-worker-clock-drift reason documented there.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '014_sync_mutations_table'
down_revision: Union[str, None] = '013_add_optimistic_version'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SERVER_UTC_NOW = "timezone('UTC', timezone('UTC', now()))"


def upgrade() -> None:
    op.create_table(
        'sync_mutations',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('client_mutation_id', sa.String(length=128), nullable=False),
        sa.Column('actor_key', sa.String(length=255), nullable=False),
        sa.Column('entity_type', sa.String(length=64), nullable=False),
        sa.Column('entity_id', sa.String(length=32), nullable=True),
        sa.Column('op', sa.String(length=16), nullable=False),
        sa.Column('outcome', sa.String(length=16), nullable=False),
        sa.Column('result_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                   server_default=sa.text(SERVER_UTC_NOW)),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('actor_key', 'client_mutation_id', name='uq_sync_mutation_actor_client_id'),
    )
    op.create_index('ix_sync_mutations_actor_key', 'sync_mutations', ['actor_key'])

    # Every migration that creates a table grants the app runtime role
    # explicit privileges on it (001_initial_schema's blanket grant only
    # covered tables that existed at that point) — same convention as
    # 004_add_notes_table / 005_generic_resource_booking.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON sync_mutations TO harmonix360_app;")
    op.execute("GRANT USAGE, SELECT ON sync_mutations_id_seq TO harmonix360_app;")


def downgrade() -> None:
    op.drop_index('ix_sync_mutations_actor_key', table_name='sync_mutations')
    op.drop_table('sync_mutations')
