"""Attendance and allocation invariants for Phase 2."""

from alembic import op

revision = "017_attendance_leave"
down_revision = "016_peoplepay360_hr"
branch_labels = None
depends_on = None


def upgrade():
    op.create_check_constraint(
        "ck_allocation_balance",
        "time_off_allocations",
        "allocated >= 0 AND taken >= 0 AND taken <= allocated",
    )
    op.create_check_constraint(
        "ck_allocation_validity",
        "time_off_allocations",
        "valid_to IS NULL OR valid_to >= valid_from",
    )
    op.create_check_constraint(
        "ck_attendance_interval",
        "attendances",
        "check_out IS NULL OR check_out >= check_in",
    )
    op.create_check_constraint(
        "ck_request_dates_duration",
        "time_off_requests",
        "date_to >= date_from AND duration >= 0",
    )


def downgrade():
    for table, name in (
        ("time_off_requests", "ck_request_dates_duration"),
        ("attendances", "ck_attendance_interval"),
        ("time_off_allocations", "ck_allocation_validity"),
        ("time_off_allocations", "ck_allocation_balance"),
    ):
        op.drop_constraint(name, table, type_="check")
