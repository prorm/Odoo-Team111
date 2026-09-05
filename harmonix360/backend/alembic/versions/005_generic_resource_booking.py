"""005_generic_resource_booking

Revision ID: 005_generic_resource_booking
Revises: 004_add_notes_table
Create Date: 2026-08-07 05:00:00.000000

Generalizes resource_bookings from an Asset-only booking table into a
polymorphic booking table for any registered resource type (see
app/services/resource_registry.py).

Schema change:
  - resource_bookings.asset_id (FK -> assets.id)  REMOVED
  - resource_bookings.resource_type (string)       ADDED
  - resource_bookings.resource_id (bigint)         ADDED
  - resource_bookings_range_overlap_excl EXCLUDE constraint recreated on
    (resource_type, resource_id, tstzrange(start_time, end_time)) instead of
    (asset_id, tstzrange(start_time, end_time)).
  - meeting_rooms table added: the minimal second resource type used to prove
    the booking table is no longer Asset-specific.

Data migration / preservation:
  This is pre-production data, so existing rows are backfilled rather than
  dropped: every existing resource_bookings row gets
  resource_type='asset', resource_id=<old asset_id> (same underlying asset,
  same integer id, just re-expressed generically). No rows are deleted and
  no booking loses its association to its asset. UPGRADE IS NON-DESTRUCTIVE.

  DOWNGRADE IS LOSSY for any booking created against a non-asset resource
  type (e.g. resource_type='meeting_room') after this migration has run —
  the pre-generalization schema has no column that can represent "book a
  meeting room", so such rows are deleted during downgrade (with a warning).
  Asset bookings round-trip losslessly in both directions.
"""
import logging
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '005_generic_resource_booking'
down_revision: Union[str, None] = '004_add_notes_table'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Drop the old asset-only EXCLUDE constraint before touching asset_id.
    op.execute("ALTER TABLE resource_bookings DROP CONSTRAINT resource_bookings_range_overlap_excl;")

    # 2. Add the new polymorphic columns (nullable for now so we can backfill).
    op.add_column('resource_bookings', sa.Column('resource_type', sa.String(length=64), nullable=True))
    op.add_column('resource_bookings', sa.Column('resource_id', sa.BigInteger(), nullable=True))

    # 3. Backfill: every existing booking was an asset booking. Preserve it exactly.
    bind.execute(sa.text(
        "UPDATE resource_bookings SET resource_type = 'asset', resource_id = asset_id"
    ))

    # 4. Now that every row is backfilled, enforce NOT NULL.
    op.alter_column('resource_bookings', 'resource_type', nullable=False)
    op.alter_column('resource_bookings', 'resource_id', nullable=False)

    op.create_index('ix_resource_bookings_resource_type', 'resource_bookings', ['resource_type'])
    op.create_index('ix_resource_bookings_resource_id', 'resource_bookings', ['resource_id'])

    # 5. Drop the Asset-specific FK + column — resource_id is polymorphic and
    # cannot carry a single-table FK.
    op.drop_constraint('resource_bookings_asset_id_fkey', 'resource_bookings', type_='foreignkey')
    op.drop_column('resource_bookings', 'asset_id')

    # 6. Recreate the double-booking guard on the generic columns.
    op.execute("""
    ALTER TABLE resource_bookings ADD CONSTRAINT resource_bookings_range_overlap_excl EXCLUDE USING gist (
      resource_type WITH =,
      resource_id WITH =,
      tstzrange(start_time, end_time) WITH &&
    );
    """)

    # 7. Minimal second resource type, to prove genericity (app/models/entities.py::MeetingRoom).
    op.create_table(
        'meeting_rooms',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('public_id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('is_bookable', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('tenant_id', sa.String(length=64), nullable=False, server_default='default'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('now()')),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_meeting_rooms_public_id', 'meeting_rooms', ['public_id'], unique=True)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON meeting_rooms TO harmonix360_app;")
    op.execute("GRANT USAGE, SELECT ON meeting_rooms_id_seq TO harmonix360_app;")


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_table('meeting_rooms')

    op.execute("ALTER TABLE resource_bookings DROP CONSTRAINT resource_bookings_range_overlap_excl;")

    # Lossy for non-asset bookings — the pre-generalization schema has no
    # equivalent column. Log what's being discarded so it's visible in output.
    orphaned = bind.execute(sa.text(
        "SELECT count(*) FROM resource_bookings WHERE resource_type != 'asset'"
    )).scalar()
    if orphaned:
        logger.warning(
            "005_generic_resource_booking downgrade: deleting %d non-asset booking(s) "
            "(resource_type != 'asset') — the pre-generalization schema cannot represent them.",
            orphaned,
        )
        bind.execute(sa.text("DELETE FROM resource_bookings WHERE resource_type != 'asset'"))

    op.add_column('resource_bookings', sa.Column('asset_id', sa.BigInteger(), nullable=True))
    bind.execute(sa.text(
        "UPDATE resource_bookings SET asset_id = resource_id WHERE resource_type = 'asset'"
    ))
    op.alter_column('resource_bookings', 'asset_id', nullable=False)
    op.create_foreign_key(
        'resource_bookings_asset_id_fkey', 'resource_bookings', 'assets', ['asset_id'], ['id']
    )

    op.drop_index('ix_resource_bookings_resource_id', table_name='resource_bookings')
    op.drop_index('ix_resource_bookings_resource_type', table_name='resource_bookings')
    op.drop_column('resource_bookings', 'resource_id')
    op.drop_column('resource_bookings', 'resource_type')

    op.execute("""
    ALTER TABLE resource_bookings ADD CONSTRAINT resource_bookings_range_overlap_excl EXCLUDE USING gist (
      asset_id WITH =,
      tstzrange(start_time, end_time) WITH &&
    );
    """)
