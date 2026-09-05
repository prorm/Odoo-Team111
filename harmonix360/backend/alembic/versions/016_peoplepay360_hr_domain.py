"""016_peoplepay360_hr_domain

Revision ID: 016_peoplepay360_hr
Revises: 015_peoplepay360_roles
Create Date: 2026-09-05 11:40:00.000000

Creates the whole PeoplePay360 HR domain (Architecture §4) and drops the
AssetFlow tables the domain replaces.

Order matters: the drops come FIRST. `employees` and `assets` do not collide by
name, but `departments` is shared, and `assets.department_id` is a foreign key
into it — leaving the AssetFlow tables in place would keep a live FK graph
pointing at tables the application no longer maps, which turns any future
`departments` change into a surprise.

DROPPED (Architecture §2, "Removed" — wrong domain, no HR equivalent):
    audit_asset_logs, discrepancies, audit_cycles, maintenance_requests,
    allocations, transfer_requests, resource_bookings, meeting_rooms, assets,
    asset_categories, notes
Dropped children-first so no FK blocks its parent.

CREATED, in dependency order:
    working_schedules, schedule_lines
    salary_rules, salary_structures, salary_structure_rules
    employees                       (FKs: users, departments, employees, working_schedules)
    contracts                       (FKs: employees, departments, salary_structures, working_schedules)
    time_off_types, time_off_allocations, time_off_requests
    attendances
    payruns, payrun_employees, payslips, payslip_lines

THE CONSTRAINT THIS MIGRATION EXISTS FOR
----------------------------------------
`contracts_active_period_overlap_excl`, Architecture §6's exact predicate:

    EXCLUDE USING gist (
      employee_id WITH =,
      daterange(start_date, end_date, '[]') WITH &&
    ) WHERE (status = 'active')

Written schema-first, before any business logic depends on it, because "payroll
resolves exactly one contract per period" (PRD A2) is an invariant application
code cannot be trusted with — two requests racing each other both pass a
SELECT-then-INSERT check and both commit. Only the database can refuse.

Three things in that definition are load-bearing:

  * `WHERE (status = 'active')` — only active rows participate. Draft, expired
    and cancelled contracts may overlap freely; they are history and proposals,
    not conflicts. `status` is `character varying` here (StrEnum with
    native_enum=False), so the predicate compares against the string literal
    'active' with no cast — which is exactly why every enum in
    app/models/enums.py stores lowercase. Storing 'ACTIVE' would make this
    predicate match nothing and silently disable the constraint.
  * `'[]'` — inclusive at both ends. A contract ending 2026-03-31 and another
    starting 2026-03-31 DO conflict: both are in force that day, so payroll
    would have two candidates for it.
  * `end_date IS NULL` — `daterange` reads a NULL upper bound as unbounded, so
    an open-ended contract correctly conflicts with everything after its start.
    This falls out of `daterange`'s own semantics; no extra clause is needed.

`btree_gist` is required for the `employee_id WITH =` half (equality on a
scalar inside a GiST index) and is already enabled by migration 001.

Consequence every service touching `contracts.status` inherits: moving a row
INTO 'active' moves it into the constrained set, so a status-only UPDATE can
raise SQLSTATE 23P01 just as an INSERT can. BaseService's docstring makes the
translation an obligation on EVERY such mutation path, not only the obvious one.

DOWNGRADE drops the HR tables and does NOT recreate the AssetFlow ones. That is
deliberate and irreversible-by-design: those tables belong to a different
product, their creating migrations (001-005) still exist upstream of this one,
and reconstructing them here would mean maintaining a second copy of a schema
nothing in this repository maps. Downgrading past this revision gets you a
database with no domain tables, which is the honest result.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "016_peoplepay360_hr"
down_revision: Union[str, None] = "015_peoplepay360_roles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONTRACT_OVERLAP_CONSTRAINT = "contracts_active_period_overlap_excl"

# Children before parents.
ASSETFLOW_TABLES = (
    "audit_asset_logs",
    "discrepancies",
    "audit_cycles",
    "maintenance_requests",
    "allocations",
    "transfer_requests",
    "resource_bookings",
    "meeting_rooms",
    "assets",
    "asset_categories",
    "notes",
)


def _audited_columns() -> list[sa.Column]:
    """The AuditedEntity mixin's columns, as Alembic sees them.

    Repeated per table rather than factored into a shared helper the models
    import, because a migration must describe the schema as it was at THIS
    revision. A migration that reads today's model definitions silently changes
    meaning every time the model changes, and stops being a reproducible
    description of history.
    """
    return [
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="default"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    ]


def _public_id_index(table: str) -> None:
    op.create_index(f"ix_{table}_public_id", table, ["public_id"], unique=True)
    op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])


def upgrade() -> None:
    # ---------------------------------------------------------------- drops
    for table in ASSETFLOW_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")

    # ------------------------------------------------------ working time
    op.create_table(
        "working_schedules",
        *_audited_columns(),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("schedule_type", sa.String(length=32), nullable=False, server_default="full_time"),
        # Server-computed from schedule_lines; never client-submitted (PS A3).
        sa.Column("weekly_hours", sa.Numeric(precision=6, scale=2), nullable=False, server_default="0.00"),
        sa.PrimaryKeyConstraint("id"),
    )
    _public_id_index("working_schedules")

    op.create_table(
        "schedule_lines",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("schedule_id", sa.BigInteger(), nullable=False),
        sa.Column("day_of_week", sa.String(length=16), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("break_minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["schedule_id"], ["working_schedules.id"], ondelete="CASCADE"),
        # Split shifts are two lines on one day, so the natural key includes
        # start_time — see app/models/working_schedule.py.
        sa.UniqueConstraint("schedule_id", "day_of_week", "start_time", name="uq_schedule_line_day_start"),
    )
    op.create_index("ix_schedule_lines_public_id", "schedule_lines", ["public_id"], unique=True)
    op.create_index("ix_schedule_lines_schedule_id", "schedule_lines", ["schedule_id"])

    # ------------------------------------------------ salary configuration
    op.create_table(
        "salary_rules",
        *_audited_columns(),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("computation_method", sa.String(length=32), nullable=False, server_default="fixed"),
        # Money is Numeric, never Float — Architecture §10.
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("percentage_base_code", sa.String(length=64), nullable=True),
        sa.Column("expression", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "tenant_id", name="uq_salary_rule_code_tenant"),
    )
    _public_id_index("salary_rules")
    op.create_index("ix_salary_rules_code", "salary_rules", ["code"])
    op.create_index("ix_salary_rules_category", "salary_rules", ["category"])

    op.create_table(
        "salary_structures",
        *_audited_columns(),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "tenant_id", name="uq_salary_structure_code_tenant"),
    )
    _public_id_index("salary_structures")

    op.create_table(
        "salary_structure_rules",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("structure_id", sa.BigInteger(), nullable=False),
        sa.Column("salary_rule_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="100"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["structure_id"], ["salary_structures.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["salary_rule_id"], ["salary_rules.id"]),
        # A rule twice in one structure would run twice and double whatever it
        # adds or deducts.
        sa.UniqueConstraint("structure_id", "salary_rule_id", name="uq_structure_rule_unique"),
    )
    op.create_index("ix_salary_structure_rules_public_id", "salary_structure_rules", ["public_id"], unique=True)
    op.create_index("ix_salary_structure_rules_structure_id", "salary_structure_rules", ["structure_id"])
    op.create_index("ix_salary_structure_rules_salary_rule_id", "salary_structure_rules", ["salary_rule_id"])

    # ------------------------------------------------------------ employees
    op.create_table(
        "employees",
        *_audited_columns(),
        sa.Column("first_name", sa.String(length=120), nullable=False),
        sa.Column("last_name", sa.String(length=120), nullable=False),
        sa.Column("work_email", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("department_id", sa.BigInteger(), nullable=True),
        sa.Column("manager_id", sa.BigInteger(), nullable=True),
        sa.Column("job_position", sa.String(length=180), nullable=True),
        sa.Column("employee_type", sa.String(length=32), nullable=False, server_default="permanent"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("default_schedule_id", sa.BigInteger(), nullable=True),
        sa.Column("hire_date", sa.Date(), nullable=True),
        sa.Column("exit_date", sa.Date(), nullable=True),
        sa.Column("bank_account", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"]),
        # Self-referential: an employee's manager is another employee.
        sa.ForeignKeyConstraint(["manager_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["default_schedule_id"], ["working_schedules.id"]),
        # One Employee per User, so the `employee_id` token claim is never
        # ambiguous about whose rows a self-scoped read may return.
        sa.UniqueConstraint("user_id", name="uq_employee_user_id"),
        sa.UniqueConstraint("work_email", "tenant_id", name="uq_employee_work_email_tenant"),
    )
    _public_id_index("employees")
    op.create_index("ix_employees_work_email", "employees", ["work_email"])
    op.create_index("ix_employees_department_id", "employees", ["department_id"])
    op.create_index("ix_employees_manager_id", "employees", ["manager_id"])
    op.create_index("ix_employees_status", "employees", ["status"])
    op.create_index("ix_employees_employee_type", "employees", ["employee_type"])
    # Kanban groups and the List filters both read (department, status).
    op.create_index("ix_employees_department_status", "employees", ["department_id", "status"])

    # ------------------------------------------------------------ contracts
    op.create_table(
        "contracts",
        *_audited_columns(),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("department_id", sa.BigInteger(), nullable=True),
        sa.Column("job_position", sa.String(length=180), nullable=True),
        sa.Column("wage", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("salary_structure_id", sa.BigInteger(), nullable=True),
        sa.Column("working_schedule_id", sa.BigInteger(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        # NULL = open-ended; daterange reads that as unbounded.
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"]),
        sa.ForeignKeyConstraint(["salary_structure_id"], ["salary_structures.id"]),
        sa.ForeignKeyConstraint(["working_schedule_id"], ["working_schedules.id"]),
    )
    _public_id_index("contracts")
    op.create_index("ix_contracts_employee_id", "contracts", ["employee_id"])
    op.create_index("ix_contracts_status", "contracts", ["status"])
    op.create_index("ix_contracts_employee_status_dates", "contracts", ["employee_id", "status", "start_date"])

    # THE constraint. Raw SQL: `daterange(...) WITH &&` over btree_gist has no
    # Core spelling that survives autogenerate, exactly as the platform
    # foundation's own range-exclusion constraint was written.
    op.execute(
        f"""
        ALTER TABLE contracts ADD CONSTRAINT {CONTRACT_OVERLAP_CONSTRAINT} EXCLUDE USING gist (
          employee_id WITH =,
          daterange(start_date, end_date, '[]') WITH &&
        ) WHERE (status = 'active');
        """
    )

    # -------------------------------------------------------------- time off
    op.create_table(
        "time_off_types",
        *_audited_columns(),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("unit", sa.String(length=16), nullable=False, server_default="days"),
        sa.Column("requires_allocation", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("payroll_integration", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "tenant_id", name="uq_time_off_type_code_tenant"),
    )
    _public_id_index("time_off_types")

    op.create_table(
        "time_off_allocations",
        *_audited_columns(),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("time_off_type_id", sa.BigInteger(), nullable=False),
        # Numeric, not Integer: half-days and hour-unit types need fractions.
        sa.Column("allocated", sa.Numeric(precision=8, scale=2), nullable=False, server_default="0.00"),
        sa.Column("taken", sa.Numeric(precision=8, scale=2), nullable=False, server_default="0.00"),
        # `remaining` is deliberately absent — it is allocated - taken, and a
        # third stored number is a third number that can disagree.
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["time_off_type_id"], ["time_off_types.id"]),
    )
    _public_id_index("time_off_allocations")
    op.create_index("ix_time_off_allocations_employee_id", "time_off_allocations", ["employee_id"])
    op.create_index("ix_time_off_allocations_time_off_type_id", "time_off_allocations", ["time_off_type_id"])
    op.create_index("ix_time_off_allocations_status", "time_off_allocations", ["status"])
    op.create_index("ix_time_off_allocations_employee_type", "time_off_allocations", ["employee_id", "time_off_type_id"])

    op.create_table(
        "time_off_requests",
        *_audited_columns(),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("time_off_type_id", sa.BigInteger(), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("duration", sa.Numeric(precision=8, scale=2), nullable=False, server_default="0.00"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("approved_by", sa.BigInteger(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("allocation_id", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["time_off_type_id"], ["time_off_types.id"]),
        # approved_by points at users, not employees: approving is an act of
        # authority held by a login, and an approver may have no Employee row.
        sa.ForeignKeyConstraint(["approved_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["allocation_id"], ["time_off_allocations.id"]),
    )
    _public_id_index("time_off_requests")
    op.create_index("ix_time_off_requests_employee_id", "time_off_requests", ["employee_id"])
    op.create_index("ix_time_off_requests_time_off_type_id", "time_off_requests", ["time_off_type_id"])
    op.create_index("ix_time_off_requests_status", "time_off_requests", ["status"])
    op.create_index("ix_time_off_requests_status_dates", "time_off_requests", ["status", "date_from", "date_to"])

    # ------------------------------------------------------------ attendance
    op.create_table(
        "attendances",
        *_audited_columns(),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("check_in", sa.DateTime(timezone=True), nullable=False),
        # NULL while still checked in. A missing checkout on a finished day is
        # a payroll warning and a validation-firewall blocker, so it must be
        # representable rather than defaulted to something plausible.
        sa.Column("check_out", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worked_hours", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="present"),
        sa.Column("corrected_by", sa.BigInteger(), nullable=True),
        sa.Column("correction_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["corrected_by"], ["users.id"]),
    )
    _public_id_index("attendances")
    op.create_index("ix_attendances_employee_id", "attendances", ["employee_id"])
    op.create_index("ix_attendances_status", "attendances", ["status"])
    op.create_index("ix_attendances_employee_check_in", "attendances", ["employee_id", "check_in"])

    # --------------------------------------------------------------- payroll
    op.create_table(
        "payruns",
        *_audited_columns(),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("salary_structure_id", sa.BigInteger(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["salary_structure_id"], ["salary_structures.id"]),
    )
    _public_id_index("payruns")
    op.create_index("ix_payruns_salary_structure_id", "payruns", ["salary_structure_id"])
    op.create_index("ix_payruns_status", "payruns", ["status"])
    op.create_index("ix_payruns_period", "payruns", ["period_start", "period_end"])

    op.create_table(
        "payrun_employees",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("payrun_id", sa.BigInteger(), nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["payrun_id"], ["payruns.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.UniqueConstraint("payrun_id", "employee_id", name="uq_payrun_employee_unique"),
    )
    op.create_index("ix_payrun_employees_public_id", "payrun_employees", ["public_id"], unique=True)
    op.create_index("ix_payrun_employees_payrun_id", "payrun_employees", ["payrun_id"])
    op.create_index("ix_payrun_employees_employee_id", "payrun_employees", ["employee_id"])

    op.create_table(
        "payslips",
        *_audited_columns(),
        sa.Column("payrun_id", sa.BigInteger(), nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        # Stored, not resolved on read: a payslip must keep showing the
        # contract that applied when it ran, even after that contract is
        # superseded.
        sa.Column("contract_id", sa.BigInteger(), nullable=False),
        sa.Column("worked_days", sa.Numeric(precision=6, scale=2), nullable=False, server_default="0.00"),
        sa.Column("gross_amount", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0.00"),
        sa.Column("net_amount", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0.00"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        # JSONB, not JSON: the validation firewall aggregates and filters these
        # across a whole payrun in the database rather than in Python.
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["payrun_id"], ["payruns.id"]),
        sa.ForeignKeyConstraint(["employee_id"], ["employees.id"]),
        sa.ForeignKeyConstraint(["contract_id"], ["contracts.id"]),
        # The database half of "a duplicate payslip attempt is caught, not
        # silently duplicated"; Idempotency-Key is the other half.
        sa.UniqueConstraint("payrun_id", "employee_id", name="uq_payslip_payrun_employee"),
    )
    _public_id_index("payslips")
    op.create_index("ix_payslips_payrun_id", "payslips", ["payrun_id"])
    op.create_index("ix_payslips_employee_id", "payslips", ["employee_id"])
    op.create_index("ix_payslips_status", "payslips", ["status"])

    op.create_table(
        "payslip_lines",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("public_id", sa.String(length=32), nullable=False),
        sa.Column("payslip_id", sa.BigInteger(), nullable=False),
        # Nullable: code/name/category are copied onto the line so a payslip
        # still renders correctly if the rule is later deleted.
        sa.Column("salary_rule_id", sa.BigInteger(), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0.00"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["payslip_id"], ["payslips.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["salary_rule_id"], ["salary_rules.id"]),
        sa.UniqueConstraint("payslip_id", "code", name="uq_payslip_line_code"),
    )
    op.create_index("ix_payslip_lines_public_id", "payslip_lines", ["public_id"], unique=True)
    op.create_index("ix_payslip_lines_payslip_id", "payslip_lines", ["payslip_id"])
    op.create_index("ix_payslip_lines_category", "payslip_lines", ["category"])

    # Migration 001 granted the runtime role privileges on the tables that
    # existed THEN; these are new, so the grant is reapplied. Without it the
    # app connects successfully and then fails "permission denied for table
    # employees" on its first query — a confusing failure a long way from its
    # cause.
    op.execute("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO harmonix360_admin;")
    op.execute("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO harmonix360_admin;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO harmonix360_app;")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO harmonix360_app;")
    # Re-assert audit-log immutability: the blanket GRANT above would otherwise
    # hand UPDATE/DELETE on audit_logs straight back to the runtime role.
    op.execute("REVOKE UPDATE, DELETE ON audit_logs FROM harmonix360_app;")
    op.execute("REVOKE UPDATE, DELETE ON audit_logs FROM PUBLIC;")


def downgrade() -> None:
    # Children before parents.
    for table in (
        "payslip_lines",
        "payslips",
        "payrun_employees",
        "payruns",
        "attendances",
        "time_off_requests",
        "time_off_allocations",
        "time_off_types",
        "contracts",
        "employees",
        "salary_structure_rules",
        "salary_structures",
        "salary_rules",
        "schedule_lines",
        "working_schedules",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE;")
    # The AssetFlow tables are NOT recreated — see the module docstring.
