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


# ---------------------------------------------------------------------------
# HR domain enums (Architecture §4)
#
# CASING: every enum below uses lowercase values, matching UserRole above. This
# is not cosmetic — Architecture §6 pins the Contract non-overlap constraint's
# predicate to `WHERE (status = 'active')`, a raw SQL string literal compared
# against the stored value. Storing 'ACTIVE' would make that predicate match
# nothing and silently disable the single most important integrity guarantee in
# the product, with no error anywhere. Lowercase everywhere removes the chance
# to get that wrong once.
#
# UserStatus and DepartmentStatus above keep their pre-existing uppercase
# values. Normalising them would be a cosmetic-only data migration on two
# columns no constraint predicate reads, which is not worth the churn; neither
# is referenced by a SQL literal anywhere.
# ---------------------------------------------------------------------------


class EmployeeStatus(str, enum.Enum):
    """Where an employee sits in their employment lifecycle.

    Distinct from Contract status: an employee is `active` while working,
    regardless of how many contracts they have had. Payroll resolves the
    contract; this drives who appears in an HR list by default.
    """

    ACTIVE = "active"
    ON_LEAVE = "on_leave"
    NOTICE_PERIOD = "notice_period"
    EXITED = "exited"


class EmployeeType(str, enum.Enum):
    """Employment category. Required by PRD A7, which makes the reporting
    dashboard filterable by Period / Department / **Employee Type**."""

    PERMANENT = "permanent"
    CONTRACT = "contract"
    INTERN = "intern"
    PART_TIME = "part_time"


class ContractStatus(str, enum.Enum):
    """Contract lifecycle.

    Only `active` rows participate in the non-overlap EXCLUDE constraint
    (Architecture §6). That is deliberate: an employee may hold any number of
    draft, expired or cancelled contracts covering the same dates — history and
    proposals are not conflicts — but never two active ones, because payroll
    must resolve exactly one contract per period (PRD A2).

    Consequence for any code that mutates `status`: moving a row INTO `active`
    moves it into the constrained set and can raise SQLSTATE 23P01. Per
    BaseService's documented contract, every mutation path that can do that
    owes the 409 translation, not just the obvious create path.
    """

    DRAFT = "draft"
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class WorkingScheduleType(str, enum.Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    FLEXIBLE = "flexible"


class Weekday(str, enum.Enum):
    """Day of the week for a ScheduleLine.

    Stored as a name rather than an integer so a schedule row is readable in a
    raw query and immune to the 0-vs-1-indexed, Sunday-vs-Monday-first
    ambiguity that silently shifts a whole week's hours.
    """

    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


#: Canonical week order, for sorting schedule lines for display and for the
#: weekly-hours computation. Relying on enum declaration order would work today
#: but breaks the moment someone reorders the class for readability.
WEEKDAY_ORDER: dict["Weekday", int] = {day: index for index, day in enumerate(Weekday)}


class AttendanceStatus(str, enum.Enum):
    """Outcome of one attendance record (PS B3).

    Operational status is server-derived, including after corrections.
    Correction provenance lives in corrected_by/correction_reason. The legacy
    half_day/corrected values remain readable, but Phase 2 does not emit them.
    """

    PRESENT = "present"
    LATE = "late"
    ABSENT = "absent"
    HALF_DAY = "half_day"
    CORRECTED = "corrected"
    OVERTIME = "overtime"
    MISSING_CHECKOUT = "missing_checkout"


class TimeOffUnit(str, enum.Enum):
    DAYS = "days"
    HOURS = "hours"


class TimeOffAllocationStatus(str, enum.Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class TimeOffRequestStatus(str, enum.Enum):
    """PS B4's approve/refuse workflow — a status field, not a state machine.

    Architecture §2 is explicit that the generic workflow engine was removed
    because this is a two-state transition; keep it that way.
    """

    DRAFT = "draft"
    TO_APPROVE = "to_approve"
    APPROVED = "approved"
    REFUSED = "refused"
    CANCELLED = "cancelled"


class SalaryRuleCategory(str, enum.Enum):
    """The buckets a payslip is presented in (PS B7)."""

    BASIC = "basic"
    ALLOWANCE = "allowance"
    GROSS = "gross"
    DEDUCTION = "deduction"
    NET = "net"


class SalaryRuleComputation(str, enum.Enum):
    """How a rule produces its amount (PS A6).

    FORMULA evaluates through `simpleeval` over named inputs — a restricted
    grammar, never `eval()` (Architecture §1). AI never participates in any of
    these; the rule engine is the sole authority for every figure on a payslip
    (Architecture §7/§10).
    """

    FIXED = "fixed"
    PERCENTAGE = "percentage"
    FORMULA = "formula"


class PayrunStatus(str, enum.Enum):
    """PS B6: Compute / Validate / Mark Paid / Send Payslips.

    `validated` and beyond are history — a finalized run is preserved, never
    recomputed in place.
    """

    DRAFT = "draft"
    COMPUTED = "computed"
    VALIDATED = "validated"
    PAID = "paid"
    CANCELLED = "cancelled"


class PayslipStatus(str, enum.Enum):
    DRAFT = "draft"
    COMPUTED = "computed"
    VALIDATED = "validated"
    PAID = "paid"
    CANCELLED = "cancelled"
