"""015_peoplepay360_role_vocabulary

Revision ID: 015_peoplepay360_roles
Revises: 014_sync_mutations_table
Create Date: 2026-09-05 11:10:00.000000

Replaces the generic asset-era role vocabulary with PeoplePay360's five roles
(PRD §3, Architecture §5):

    EMPLOYEE         -> employee
    ASSET_MANAGER    -> hr_manager
    DEPARTMENT_HEAD  -> hr_manager
    ADMIN            -> admin

Why this is a plain UPDATE and not an ALTER TYPE
------------------------------------------------
`users.role` is `character varying(32)`, not a Postgres enum — the model
declares it with `StrEnum(..., native_enum=False)` (app/models/types.py), which
stores the enum's `.value` as a string. So changing the vocabulary is a data
migration plus a new column default, with no type surgery, no `ALTER TYPE ...
ADD VALUE` transaction restrictions, and no dependency on migration ordering
across the two.

Mapping rationale
-----------------
ASSET_MANAGER and DEPARTMENT_HEAD both collapse onto `hr_manager` because that
is the closest-fitting role in the new matrix that is not a privilege
escalation: both were "manages records for other people, no financial
authority", which is exactly HR Manager (full HR CRUD, explicitly no payroll
access). Mapping either to a payroll role would silently hand somebody access
to salary data on the strength of an old asset-management grant — the one
outcome this migration must not produce. Any row holding a value not in the
map is coerced to `employee`, the least-privileged role, for the same reason:
an unrecognised grant fails closed.

In practice this repository has no such rows to convert — the role vocabulary
changed before any PeoplePay360 user existed — but the UPDATE is written to be
correct against a database seeded from the platform-foundation era rather than
assuming an empty table.

DOWNGRADE maps back to the pre-PeoplePay360 vocabulary. It is lossy and says
so: the three HR/payroll roles all collapse onto ASSET_MANAGER, because the
old vocabulary has no concept of payroll authority to preserve them in.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "015_peoplepay360_roles"
down_revision: Union[str, None] = "014_sync_mutations_table"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_MAP = {
    "EMPLOYEE": "employee",
    "ASSET_MANAGER": "hr_manager",
    "DEPARTMENT_HEAD": "hr_manager",
    "ADMIN": "admin",
}

NEW_ROLES = ("employee", "hr_manager", "hr_payroll_user", "hr_payroll_manager", "admin")

DOWNGRADE_MAP = {
    "employee": "EMPLOYEE",
    "hr_manager": "ASSET_MANAGER",
    "hr_payroll_user": "ASSET_MANAGER",
    "hr_payroll_manager": "ASSET_MANAGER",
    "admin": "ADMIN",
}


def _remap(mapping: dict[str, str], fallback: str) -> None:
    """Rewrite users.role through `mapping`, coercing anything unmapped to
    `fallback` (always the least-privileged role in the target vocabulary)."""
    conn = op.get_bind()
    for old, new in mapping.items():
        conn.execute(sa.text("UPDATE users SET role = :new WHERE role = :old"), {"new": new, "old": old})

    # `= ANY(:known)` with an array parameter rather than `IN :known`: asyncpg
    # binds a single positional placeholder per parameter and cannot expand a
    # Python tuple into an IN-list, so the IN form is a syntax error at execute
    # time. An array comparison passes one parameter and needs no expansion.
    conn.execute(
        sa.text("UPDATE users SET role = :fallback WHERE role <> ALL(:known)"),
        {"fallback": fallback, "known": sorted(set(mapping.values()))},
    )


def upgrade() -> None:
    # Default first: a concurrent INSERT that lands between the UPDATE and the
    # default change would otherwise write the stale 'EMPLOYEE' and survive the
    # remap. Changing the default first means the worst case is a row that is
    # already correct.
    op.alter_column("users", "role", server_default="employee", existing_type=sa.String(length=32))
    _remap(UPGRADE_MAP, fallback="employee")

    # A CHECK constraint, not a Postgres enum: same fail-closed guarantee at
    # write time, while keeping the next vocabulary change a one-line DROP/ADD
    # rather than an ALTER TYPE. Without it, a typo'd role string inserted by
    # hand would be read back by app/api/v1/deps.py, fail to parse, and quietly
    # degrade that user to `employee` — a confusing lockout instead of a loud
    # rejection at the point of the mistake.
    op.create_check_constraint(
        "ck_users_role_valid",
        "users",
        sa.text("role IN " + str(NEW_ROLES)),
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_role_valid", "users", type_="check")
    op.alter_column("users", "role", server_default="EMPLOYEE", existing_type=sa.String(length=32))
    _remap(DOWNGRADE_MAP, fallback="EMPLOYEE")
