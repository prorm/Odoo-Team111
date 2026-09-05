"""010_booking_exclusion_predicate

Revision ID: 010_booking_exclusion_predicate
Revises: 005_generic_resource_booking
Create Date: 2026-08-10 10:00:00.000000

Why the numbering jumps 005 -> 010
----------------------------------
This migration follows 005 directly; 006-009 do not exist in Harmonix360 and never
will. The gap is deliberate. The GlobeTrotter fork branched from
005_generic_resource_booking and took 006_globetrotter_domain for its own first
migration, so a Harmonix360 migration numbered 006 would sit beside a fork
migration numbered 006, both claiming to be "the one after 005" — ambiguous the
moment anyone diffs the two repos or reads a stamped alembic_version out of a
shared database. Starting core's post-fork work at 010 leaves the fork 006-009
and keeps the two sequences visually unambiguous.

Note this is a READABILITY fix, not a merge fix. Both chains still declare
down_revision = '005_generic_resource_booking', so a repo containing both would
have two heads at 005 and need an explicit `alembic merge` revision regardless
of what these are numbered.

Adds the missing WHERE predicate to resource_bookings_range_overlap_excl.

Bug this fixes
--------------
The constraint created in 001 (and rebuilt on the generic columns in 005) had
NO predicate:

    EXCLUDE USING gist (
      resource_type WITH =,
      resource_id   WITH =,
      tstzrange(start_time, end_time) WITH &&
    )

so EVERY row in resource_bookings occupied its time range in the GiST index
regardless of lifecycle state. Concretely, that meant:

  - a CANCELLED booking still blocked the slot it no longer holds, and
  - a soft-deleted booking (deleted_at IS NOT NULL — invisible to
    BaseRepository, which filters deleted_at IS NULL everywhere) still blocked
    the slot, with no row the API could show to explain the 409.

The second case is the worse one: the slot was unbookable forever and nothing
reachable through the application could release it.

Target semantics (per app/services/booking.py, which treats SQLSTATE 23P01 as
"already booked"): only rows that are BOTH live and not cancelled reserve their
range.

    WHERE (deleted_at IS NULL AND status <> 'CANCELLED')

`status` is character varying(32) here (StrEnum(..., native_enum=False) stores
the enum's .value), so the predicate compares against the string 'CANCELLED'
directly — there is no Postgres enum type to cast.

Note on PENDING: PENDING bookings deliberately stay inside the predicate. A
booking awaiting AI/human review (app/jobs/tasks/booking_decision.py) holds its
slot until it is explicitly CANCELLED; that is the pre-existing behaviour and
this migration does not change it.

Data impact
-----------
UPGRADE is strictly widening: the new constraint accepts every row set the old
one accepted, plus more. It cannot fail on existing data, and no row is read,
rewritten, or deleted. The GiST index backing the constraint is rebuilt (drop +
add), which is the only physical change.

DOWNGRADE is narrowing and CAN legitimately fail: if, while the predicate was
in place, someone booked a slot overlapping a CANCELLED or soft-deleted
booking, the unpredicated constraint has no way to accept both rows. Rather
than silently deleting one of them, downgrade() lets Postgres raise
exclusion_violation and reports which pairs conflict. Resolve by hard-deleting
or re-timing the offending rows, then re-run the downgrade.
"""
from typing import Sequence, Union
from alembic import op

revision: str = '010_booking_exclusion_predicate'
down_revision: Union[str, None] = '005_generic_resource_booking'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT_NAME = "resource_bookings_range_overlap_excl"


def upgrade() -> None:
    op.execute(f"ALTER TABLE resource_bookings DROP CONSTRAINT {CONSTRAINT_NAME};")
    op.execute(f"""
    ALTER TABLE resource_bookings ADD CONSTRAINT {CONSTRAINT_NAME} EXCLUDE USING gist (
      resource_type WITH =,
      resource_id WITH =,
      tstzrange(start_time, end_time) WITH &&
    ) WHERE (deleted_at IS NULL AND status <> 'CANCELLED');
    """)


def downgrade() -> None:
    op.execute(f"ALTER TABLE resource_bookings DROP CONSTRAINT {CONSTRAINT_NAME};")
    # Narrowing: may raise 23P01 if rows created under the predicate now
    # conflict. See the module docstring — failing loudly beats deleting data.
    op.execute(f"""
    ALTER TABLE resource_bookings ADD CONSTRAINT {CONSTRAINT_NAME} EXCLUDE USING gist (
      resource_type WITH =,
      resource_id WITH =,
      tstzrange(start_time, end_time) WITH &&
    );
    """)
