"""Persist payslip references/inputs and blocking compute findings.

No live-data backfill: the wage at compute time cannot be inferred from a
mutable current Contract. Existing monetary columns and lines are untouched.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "018_payroll_snapshots"
down_revision = "017_attendance_leave"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "payslips", sa.Column("reference_snapshot", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "payslips", sa.Column("context_snapshot", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "payruns", sa.Column("computation_warnings", postgresql.JSONB(), nullable=True)
    )


def downgrade():
    op.drop_column("payruns", "computation_warnings")
    op.drop_column("payslips", "context_snapshot")
    op.drop_column("payslips", "reference_snapshot")
