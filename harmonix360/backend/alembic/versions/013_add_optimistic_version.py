"""013_add_optimistic_version

Revision ID: 013_add_optimistic_version
Revises: 012_money_as_numeric
Create Date: 2026-09-03 09:00:00.000000

Adds a REAL `version` column back to every table that uses AuditedEntity —
the TODO left by `011_drop_decorative_version` / HARMONIX360_ARCHITECTURE.md
Section 2 rule 10, triggered by the offline-sync unfreeze (§13.3): a client
that queued a mutation while offline needs the server to detect that the row
it read has since changed, and rule 10 already named the correct mechanism.

Unlike the 001-010 column, this one is read: `app/models/mixins.py` maps it
as SQLAlchemy's `__mapper_args__["version_id_col"]`, so the ORM itself adds
`WHERE version = :loaded_version` to every UPDATE and bumps it — verified via
`StaleDataError` in `app/repositories/base.py`, not asserted.

Scope: only the five tables that actually go through BaseRepository/BaseService
today (the ones mixing in AuditedEntity) — same scoping methodology
011_drop_decorative_version used (enumerate from information_schema, don't
guess). `users`, `departments`, `allocations`, `maintenance_requests`,
`audit_cycles`, `asset_categories` predate AuditedEntity, have no repository,
and are out of scope for "generic offline sync" the same way they were never
part of the original decorative column's cleanup rationale — they're a
separate, not-yet-made decision.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '013_add_optimistic_version'
down_revision: Union[str, None] = '012_money_as_numeric'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The five AuditedEntity-mixin tables (app/models/entities.py), verified by
# grep for `AuditedEntity, Base` — not the 11 migration 011 touched.
TABLES_WITH_AUDITED_ENTITY = (
    'assets',
    'transfer_requests',
    'resource_bookings',
    'meeting_rooms',
    'notes',
)


def upgrade() -> None:
    for table in TABLES_WITH_AUDITED_ENTITY:
        op.add_column(
            table,
            sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        )


def downgrade() -> None:
    for table in TABLES_WITH_AUDITED_ENTITY:
        op.drop_column(table, 'version')
