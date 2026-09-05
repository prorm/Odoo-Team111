"""011_drop_decorative_version

Revision ID: 011_drop_decorative_version
Revises: 010_booking_exclusion_predicate
Create Date: 2026-08-10 10:30:00.000000

Drops the `version` column from every table that carried it.

Why
---
`version INTEGER NOT NULL DEFAULT 1` was created on 11 tables (by 001, 004 and
005) and inherited by every entity through `AuditedEntity`. It was never wired
to anything: a repo-wide search found no code that reads it, increments it,
compares it in a WHERE clause, exposes it in a Pydantic schema, or registers it
as SQLAlchemy's `__mapper_args__["version_id_col"]`. The single reference in the
whole codebase was a debug `print()` in `app/verify_scaffolding.py`.

The column therefore advertised optimistic concurrency control that Harmonix360
does not perform — the actively misleading case, since a reviewer (or a fork
author) reasonably reads `version` as "lost updates are handled here" when
concurrent writers silently overwrite each other. Removing it makes the absence
of optimistic locking honest.

This migration does NOT add optimistic locking. That is a deliberate,
separately-scoped decision; see the TODO in HARMONIX360_ARCHITECTURE.md Section 2.

Affected tables (all 11, enumerated from information_schema before writing this
migration — every table where column_name = 'version')
-------------------------------------------------------------------------------
  allocations             maintenance_requests
  asset_categories        meeting_rooms
  assets                  notes
  audit_cycles            resource_bookings
  departments             transfer_requests
  users

Tables that never had the column, and are untouched here: audit_logs,
audit_asset_logs, discrepancies, notifications, activity_logs.

Data impact
-----------
The column's values are discarded. This is a genuine (if empty) data loss, so
it was checked rather than assumed: on the development database every one of
these 11 tables reported `count(*) FILTER (WHERE version <> 1) = 0` — i.e. no
row anywhere had ever moved off the default, consistent with nothing ever
incrementing it. No other column, row, index or constraint is touched.

DOWNGRADE restores the column on all 11 tables with the original
`INTEGER NOT NULL DEFAULT 1`, which reproduces the pre-drop state exactly given
that every value was 1.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '011_drop_decorative_version'
down_revision: Union[str, None] = '010_booking_exclusion_predicate'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Every table carrying `version`, verified against information_schema.columns
# on the migrated development database before this migration was written.
TABLES_WITH_VERSION = (
    'allocations',
    'asset_categories',
    'assets',
    'audit_cycles',
    'departments',
    'maintenance_requests',
    'meeting_rooms',
    'notes',
    'resource_bookings',
    'transfer_requests',
    'users',
)


def upgrade() -> None:
    for table in TABLES_WITH_VERSION:
        op.drop_column(table, 'version')


def downgrade() -> None:
    for table in TABLES_WITH_VERSION:
        op.add_column(
            table,
            sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        )
