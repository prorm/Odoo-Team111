"""Track each employee's delivery without changing payroll results."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "019_payslip_delivery"
down_revision = "018_payroll_snapshots"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "payslip_deliveries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "payslip_id",
            sa.BigInteger(),
            sa.ForeignKey("payslips.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "payrun_id",
            sa.BigInteger(),
            sa.ForeignKey("payruns.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'failed')", name="ck_delivery_status"
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_delivery_attempts"),
    )
    op.create_index(
        "ix_payslip_deliveries_payrun_id", "payslip_deliveries", ["payrun_id"]
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON payslip_deliveries TO harmonix360_app"
    )
    op.execute("GRANT ALL ON payslip_deliveries TO harmonix360_admin")


def downgrade():
    op.drop_table("payslip_deliveries")
