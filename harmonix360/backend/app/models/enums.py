import enum


class UserRole(str, enum.Enum):
    """PeoplePay360's five roles — PRD §3, Architecture §5.

    Exactly these five, no more. The generic asset-era vocabulary
    (ASSET_MANAGER, DEPARTMENT_HEAD) is gone: those roles described who could
    move equipment around, which is not a question this product asks.

    Values are lowercase because Architecture §4 writes them that way
    (`employee | hr_manager | hr_payroll_user | hr_payroll_manager | admin`)
    and because they are stored as strings (StrEnum, `native_enum=False`), so
    the stored value is what appears in a JWT claim, an audit row and a
    database predicate alike. One casing everywhere means no normalisation
    layer anyone can forget to apply.

    The ordering below is the escalation ladder, and `ROLE_HIERARCHY` turns it
    into the "all rights of the role beneath, plus" relation the PRD's role
    table describes. Do not infer authority from declaration order anywhere
    else — use `role_at_least`.
    """

    EMPLOYEE = "employee"
    HR_MANAGER = "hr_manager"
    HR_PAYROLL_USER = "hr_payroll_user"
    HR_PAYROLL_MANAGER = "hr_payroll_manager"
    ADMIN = "admin"


# Escalation ladder from PRD §3. Higher number = strictly more authority, and
# every role holds all rights of every role below it EXCEPT for one deliberate
# discontinuity worth stating out loud:
#
#   HR Manager is NOT below HR Payroll User on the HR axis — it has the same
#   full HR CRUD. The ladder only ascends on the payroll axis (HR Manager has
#   no payroll access at all; HR Payroll User adds CRU on payruns/payslips and
#   read on salary config; HR Payroll Manager adds full CRUD on both).
#
# That is why `require_role` takes an explicit set of allowed roles rather than
# a single minimum: "HR Manager or above" and "HR Payroll User or above" are
# different questions, and only the endpoint knows which it is asking.
ROLE_HIERARCHY: dict["UserRole", int] = {
    UserRole.EMPLOYEE: 0,
    UserRole.HR_MANAGER: 1,
    UserRole.HR_PAYROLL_USER: 2,
    UserRole.HR_PAYROLL_MANAGER: 3,
    UserRole.ADMIN: 4,
}

# Convenience sets, named after the question each endpoint is actually asking.
# Prefer these over hand-listing roles at a call site: a role added later gets
# picked up in one place instead of being missed in the one router nobody
# remembered to update.

#: Full CRUD on Employees, Contracts, Working Schedules, Attendance, Time Off
#: (Architecture §5). Note that HR Payroll User/Manager are included — they
#: hold all HR Manager rights.
HR_ROLES = frozenset({
    UserRole.HR_MANAGER,
    UserRole.HR_PAYROLL_USER,
    UserRole.HR_PAYROLL_MANAGER,
    UserRole.ADMIN,
})

#: Read on Salary Structures/Rules; CRU on Payruns/Payslips.
PAYROLL_ROLES = frozenset({
    UserRole.HR_PAYROLL_USER,
    UserRole.HR_PAYROLL_MANAGER,
    UserRole.ADMIN,
})

#: Full CRUD on Salary Structures/Rules and Payruns/Payslips — authoring the
#: rules that decide what people are paid, not just running them.
PAYROLL_ADMIN_ROLES = frozenset({
    UserRole.HR_PAYROLL_MANAGER,
    UserRole.ADMIN,
})

#: User management and role assignment.
ADMIN_ROLES = frozenset({UserRole.ADMIN})

#: Every role, i.e. "any authenticated user" — used by endpoints that are
#: self-scoped rather than role-scoped (an Employee reading their own profile,
#: attendance or leave balance). The row-level "…but only your own" half of
#: that rule is enforced in the service layer, never by the role check alone.
ALL_ROLES = frozenset(UserRole)


def role_at_least(role: "UserRole", minimum: "UserRole") -> bool:
    """True when `role` sits at or above `minimum` on ROLE_HIERARCHY.

    Only meaningful along a single axis; see the discontinuity noted on
    ROLE_HIERARCHY before using it to answer an HR-vs-payroll question.
    """
    return ROLE_HIERARCHY[role] >= ROLE_HIERARCHY[minimum]


class UserStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class DepartmentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class AuditActorType(str, enum.Enum):
    """Who performed an audited action. Used by the AI/MCP layer in Phase 9,
    where an agent-initiated mutation is audited with the same shape as a human
    one (Architecture §11)."""

    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"
    AI = "AI"
