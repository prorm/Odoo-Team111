"""Seed the demo dataset.

Idempotent by design — running it twice must not create a second copy of
anything, because it is run by hand during development and again right before
a demo, and a duplicated Employee is exactly the kind of thing that only
surfaces on stage.

    python -m app.seed

Seeds the identity layer, a demo salary structure including Loss of Pay, a
five-person roster across four departments, one FINALIZED (paid) payrun over
July 2026, and the INPUTS for a live Loss-of-Pay payroll scenario over August
2026. One login per role from PRD §3 plus the departments the HR domain hangs
off.

HOW THE PAYSLIPS GET WRITTEN, AND WHY IT MATTERS
------------------------------------------------
No Payrun, Payslip or PayslipLine is ever written by hand here. A payslip
written by anything other than the compute engine is a payslip with no
`reference_snapshot`, and migration 018 makes exactly those rows return 409
`historical_snapshot_unavailable` on every read (see progress.md's gap-fix
section) — so a hand-built "paid" payslip would seed a demo whose payslips
cannot be opened, printed or emailed.

The July run is therefore driven through the real `PayrunService`: create →
compute → validate → mark paid, the same four calls PS B6's buttons make, and
`_verify_pdf` then renders one of the resulting payslips and checks the bytes
really are a PDF. That is the whole of PRD §11's "paid payrun with a generated
PDF" — generated, not asserted.

Nothing stores the PDF. `/payslips/{id}/pdf` renders on demand from the
persisted snapshot, so a cached file would be a second, staler copy of money
that is supposed to have exactly one source.

WHY AUGUST IS LEFT UNCOMPUTED
------------------------------
PRD §7's first acceptance scenario is run LIVE. If the seed had already
computed August, that live run would collide with its own demo data — every
employee would come back carrying a blocking `duplicate_payslip` finding,
which is the firewall working correctly and the demo failing anyway. July is
seeded and paid; August is left as inputs for a human to press Compute on.

THE LIVE SCENARIO, AND WHY THESE NUMBERS
-----------------------------------------
`_seed_lop_scenario` creates one employee whose August 2026 payrun exercises
Gap-fix's `LOP_AMOUNT` rule end to end:

    contract wage                     30,000.00
    August 2026 scheduled workdays           21   (Mon-Fri; Aug 1 2026 is a Sat)
    approved unpaid leave      12-14 Aug =    3   calendar days in the period

    LOP_AMOUNT = (30000 / 21) * 3           = 4,285.71  (ROUND_HALF_UP)
    Basic 30,000.00 + HRA 12,000.00         = Gross 42,000.00
    Net = 42,000.00 - 200.00 - 4,285.71     = 37,514.29

Those are deliberately the same figures as the hand-computed golden test the
gap-fix phase added, so the demo on screen and the number pinned in the test
suite are the same arithmetic — if the demo ever shows something else, a test
is already failing.

The employee has a bank account and a working schedule ON PURPOSE. Without the
first, every payslip carries a blocking `missing_bank_details` warning; without
the second, `LOP_AMOUNT` is omitted entirely and a blocking
`lop_schedule_unavailable` warning fires. A demo dataset that cannot be
validated is not a demo dataset.
"""

import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import encode_public_id, get_password_hash
from app.models.attendance import Attendance
from app.models.contract import Contract
from app.models.department import Department
from app.models.employee import Employee
from app.models.enums import (
    ContractStatus,
    EmployeeType,
    TimeOffRequestStatus,
    TimeOffUnit,
    UserRole,
    Weekday,
    WorkingScheduleType,
)
from app.models.payroll import Payrun
from app.models.time_off import TimeOffRequest, TimeOffType
from app.models.user import User
from app.models.salary import SalaryRule, SalaryStructure
from app.repositories.hr import (
    AttendanceRepository,
    ContractRepository,
    EmployeeRepository,
    TimeOffRequestRepository,
    TimeOffTypeRepository,
)
from app.schemas.payroll import PayrunCreate
from app.schemas.salary import SalaryRuleCreate, SalaryStructureCreate
from app.schemas.schedule import ScheduleLineInput
from app.models.working_schedule import WorkingSchedule
from app.services.attendance import (
    derive_attendance_status,
    schedule_expectations,
    worked_hours,
)
from app.services.payroll import PayrunService, PayslipService
from app.services.payslip_documents import document_pdf
from app.services.salary import SalaryRuleService, SalaryStructureService
from app.services.schedule import WorkingScheduleService
from app.services.time_off import request_duration

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("peoplepay360.seed")

#: Every audit row this file writes carries this actor, so a demo dataset
#: is distinguishable from something a person did in the UI.
SEED_ACTOR = "seed@peoplepay360.com"

# One login per role, so the RBAC matrix can be demonstrated by logging in
# rather than by describing it. Passwords are obviously weak on purpose: this
# is demo data and the file is in version control. Nothing here is a production
# credential path — real accounts are created through the admin UI.
DEMO_USERS: list[tuple[str, str, str, UserRole]] = [
    ("admin@peoplepay360.com", "admin123", "Aditi Rao (Admin)", UserRole.ADMIN),
    (
        "payroll.manager@peoplepay360.com",
        "payroll123",
        "Meera Iyer (Payroll Manager)",
        UserRole.HR_PAYROLL_MANAGER,
    ),
    (
        "payroll.user@peoplepay360.com",
        "payroll123",
        "Sanjay Nair (Payroll User)",
        UserRole.HR_PAYROLL_USER,
    ),
    (
        "hr.manager@peoplepay360.com",
        "hrmanager123",
        "Priya Sharma (HR Manager)",
        UserRole.HR_MANAGER,
    ),
    (
        # Named for the Loss-of-Pay employee on purpose: `_seed_lop_scenario`
        # links this login to that Employee row, and a login labelled with one
        # person's name while its self-service screens show another's is the
        # kind of detail that derails a demo mid-sentence.
        "employee@peoplepay360.com",
        "employee123",
        "Lakshmi Prasad (Employee)",
        UserRole.EMPLOYEE,
    ),
]

#: The login `_seed_lop_scenario` attaches its Employee to. Without a link the
#: token carries no `employee_id` claim, and every "own records only" screen —
#: own profile, own attendance check-in, own time-off request — has nothing
#: server-signed to scope by. There is deliberately no API that sets
#: `Employee.user_id` (Architecture §5 gives account management to Admin, and
#: `EmployeeCreate` excludes the field so an HR Manager cannot attach an
#: employee to an account outranking their own), so the ONLY way to demo the
#: Employee role without editing the database by hand is to seed the link.
EMPLOYEE_LOGIN_EMAIL = "employee@peoplepay360.com"

DEMO_DEPARTMENTS: list[tuple[str, str]] = [
    ("Engineering", "ENG"),
    ("Sales", "SALES"),
    ("Human Resources", "HR"),
    ("Finance", "FIN"),
]

# --------------------------------------------------------------------------
# The Loss-of-Pay scenario's fixed inputs. Constants rather than literals
# buried in the function, so the arithmetic in the module docstring can be
# checked against them without reading the code that uses them.
# --------------------------------------------------------------------------

#: Natural keys. Every one of these is what makes a re-run idempotent: the
#: seed looks the row up by this value and skips creation if it is there.
LOP_EMPLOYEE_EMAIL = "lop.demo@peoplepay360.com"
LOP_SCHEDULE_NAME = "PP360 Demo Full-Time (Mon-Fri)"
LOP_LEAVE_TYPE_CODE = "PP360_UNPAID"

LOP_CONTRACT_WAGE = Decimal("30000.00")
#: Open-ended and starting well before the demo period, so the contract covers
#: the whole of it and no `contract_gap` warning fires.
LOP_CONTRACT_START = date(2026, 1, 1)

#: August 2026: 21 Mon-Fri dates (Aug 1 is a Saturday, Aug 31 a Monday).
LOP_PERIOD_START = date(2026, 8, 1)
LOP_PERIOD_END = date(2026, 8, 31)
#: Wednesday to Friday — three calendar days, all of them scheduled workdays.
LOP_LEAVE_FROM = date(2026, 8, 12)
LOP_LEAVE_TO = date(2026, 8, 14)

#: 09:00-17:00 with an hour of break = 7 net hours a day, 35 a week.
LOP_WORKDAYS = [
    Weekday.MONDAY,
    Weekday.TUESDAY,
    Weekday.WEDNESDAY,
    Weekday.THURSDAY,
    Weekday.FRIDAY,
]
LOP_SHIFT_START = time(9, 0)
LOP_SHIFT_END = time(17, 0)
LOP_BREAK_MINUTES = 60
#: Attendance is recorded 09:00-16:00 — exactly the 7 NET hours the schedule
#: expects, so `derive_attendance_status` returns PRESENT rather than
#: OVERTIME. Checked out on every day, so no `missing_checkout` warning.
LOP_ATTENDANCE_OUT = time(16, 0)

# --------------------------------------------------------------------------
# The rest of the demo roster, and the already-paid payrun over it.
#
# WHY A SECOND, EARLIER PERIOD. The Loss-of-Pay scenario above is deliberately
# left UNCOMPUTED: PRD §7's first acceptance scenario is run live, on stage,
# and it ends at a payslip. If the seed had already computed August 2026, the
# live run would collide with its own demo data — every employee would come
# back carrying a blocking `duplicate_payslip` finding, which is the firewall
# working correctly and the demo failing anyway.
#
# So the seeded PAID run is July 2026 and the live one is August 2026. They do
# not overlap, so no duplicate finding fires; and the July run is exactly the
# thing PRD §11 asks the dataset to contain — a finalized payrun with a real,
# renderable PDF behind it — without consuming the period the demo needs.
DEMO_PAID_PERIOD_START = date(2026, 7, 1)
DEMO_PAID_PERIOD_END = date(2026, 7, 31)
#: Natural key. `_seed_paid_payrun` looks the run up by this name and does
#: nothing at all if it is already there, which is what keeps a second
#: `python -m app.seed` from computing a second July.
DEMO_PAID_PAYRUN_NAME = "July 2026 payroll (demo)"

#: Four more employees so PS B9's department widgets have more than one bar to
#: draw. "Salary Cost by Department" and the department breakdown are named
#: acceptance criteria; against a single-employee database they render as one
#: column and read as broken rather than as empty. Every one of them has a
#: department, a schedule and a bank account ON PURPOSE — those three are what
#: separate a payrun that validates from one that stops at the firewall.
#:
#: (first, last, work email, department code, job position, type, wage)
DEMO_ROSTER: list[tuple[str, str, str, str, str, EmployeeType, Decimal]] = [
    (
        "Arjun",
        "Menon",
        "arjun.menon@peoplepay360.com",
        "ENG",
        "Senior Engineer",
        EmployeeType.PERMANENT,
        Decimal("45000.00"),
    ),
    (
        "Divya",
        "Rao",
        "divya.rao@peoplepay360.com",
        "SALES",
        "Account Executive",
        # Deliberately not PERMANENT: PS A7 makes the dashboard filterable by
        # Employee Type, and a filter every row satisfies demonstrates nothing.
        EmployeeType.CONTRACT,
        Decimal("38000.00"),
    ),
    (
        "Kabir",
        "Shah",
        "kabir.shah@peoplepay360.com",
        "HR",
        "HR Generalist",
        EmployeeType.PERMANENT,
        Decimal("32000.00"),
    ),
    (
        "Nisha",
        "Gupta",
        "nisha.gupta@peoplepay360.com",
        "FIN",
        "Finance Analyst",
        EmployeeType.PERMANENT,
        Decimal("52000.00"),
    ),
]


async def _seed_departments(db) -> int:
    created = 0
    for name, code in DEMO_DEPARTMENTS:
        existing = (
            await db.execute(
                select(Department).where(
                    Department.code == code, Department.tenant_id == "default"
                )
            )
        ).scalar_one_or_none()
        if existing:
            continue
        dept = Department(public_id="temp", name=name, code=code)
        db.add(dept)
        await db.flush()
        dept.public_id = encode_public_id(dept.id, "dept")
        created += 1
    await db.commit()
    return created


async def _seed_users(db) -> int:
    created = 0
    for email, password, name, role in DEMO_USERS:
        existing = (
            await db.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
        if existing:
            # Re-assert the role: the role vocabulary changed in Phase 0 step 3,
            # so a database seeded before that holds rows whose `role` no longer
            # parses. Fixing it here keeps re-running the seed a valid repair.
            if existing.role != role:
                existing.role = role
            # Same reasoning for the display name, which Phase 7 changed so the
            # employee login matches the Employee row it is now linked to.
            if existing.name != name:
                existing.name = name
            continue
        user = User(
            public_id="temp",
            email=email,
            password_hash=get_password_hash(password),
            name=name,
            role=role,
        )
        db.add(user)
        await db.flush()
        user.public_id = encode_public_id(user.id, "usr")
        created += 1
    await db.commit()
    return created


async def _seed_salary_structure(db):
    """Create the demo only when absent; never overwrite authored/paid rules.

    Wage -> Basic; HRA=40% Basic; Gross=Basic+HRA; PT=200; Loss of Pay
    uses the computed LOP_AMOUNT; Net=Gross-PT-LOP. The caller owns commit.
    Existing rules/structure are preserved on repeated runs.
    """
    existing = (
        await db.execute(
            select(SalaryStructure).where(
                SalaryStructure.code == "PP360_DEMO",
                SalaryStructure.tenant_id == "default",
            )
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    definitions = [
        dict(
            code="PP360_BASIC",
            name="Basic",
            category="basic",
            computation_method="formula",
            expression="CONTRACT_WAGE",
        ),
        dict(
            code="PP360_HRA",
            name="House Rent Allowance",
            category="allowance",
            computation_method="percentage",
            amount="40.00",
            percentage_base_code="PP360_BASIC",
        ),
        dict(
            code="PP360_GROSS",
            name="Gross",
            category="gross",
            computation_method="formula",
            expression="PP360_BASIC + PP360_HRA",
        ),
        dict(
            code="PP360_PT",
            name="Professional Tax (demo)",
            category="deduction",
            computation_method="fixed",
            amount="200.00",
        ),
        dict(
            code="PP360_LOP",
            name="Loss of Pay",
            category="deduction",
            computation_method="formula",
            expression="LOP_AMOUNT",
        ),
        dict(
            code="PP360_NET",
            name="Net",
            category="net",
            computation_method="formula",
            expression="PP360_GROSS - PP360_PT - PP360_LOP",
        ),
    ]
    links = []
    for index, definition in enumerate(definitions, start=1):
        rule = (
            await db.execute(
                select(SalaryRule).where(
                    SalaryRule.code == definition["code"],
                    SalaryRule.tenant_id == "default",
                )
            )
        ).scalar_one_or_none()
        if rule is None:
            rule = await SalaryRuleService(db).create_rule(
                SalaryRuleCreate(**definition, sequence=index * 10),
                actor_email="seed@peoplepay360.com",
            )
        links.append({"salary_rule_id": rule.public_id, "sequence": index * 10})
    return await SalaryStructureService(db).create_structure(
        SalaryStructureCreate(
            name="PeoplePay360 Demo Salary", code="PP360_DEMO", rules=links
        ),
        actor_email="seed@peoplepay360.com",
    )


async def _seed_lop_scenario(db, structure) -> dict:
    """The Loss-of-Pay demo inputs: schedule, employee, contract, approved
    unpaid leave, and the attendance that makes the period look worked.

    Idempotent the same way everything else here is — each row is looked up by
    a natural key first (`LOP_EMPLOYEE_EMAIL`, `LOP_SCHEDULE_NAME`,
    `LOP_LEAVE_TYPE_CODE`, and for the contract, the employee it belongs to).
    Re-running must not create a second active contract in particular: two of
    those overlapping is precisely what `contracts_active_period_overlap_excl`
    refuses, so a non-idempotent seed would not merely duplicate data, it would
    crash on its second run.

    Returns what was created, so the caller can report it honestly rather than
    claiming a scenario that was already there.
    """
    created: dict[str, bool] = {}

    # --- working schedule -------------------------------------------------
    # Through the real service, because `weekly_hours` is server-computed and
    # `compute_weekly_hours` is its only writer (PS A3: "never manually
    # entered"). Setting the column here would be a second implementation of
    # the one calculation that file exists to own.
    schedule = (
        await db.execute(
            select(WorkingSchedule).where(
                WorkingSchedule.name == LOP_SCHEDULE_NAME,
                WorkingSchedule.tenant_id == "default",
                WorkingSchedule.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if schedule is None:
        schedule = await WorkingScheduleService(db).create_schedule(
            name=LOP_SCHEDULE_NAME,
            schedule_type=WorkingScheduleType.FULL_TIME,
            lines=[
                ScheduleLineInput(
                    day_of_week=day,
                    start_time=LOP_SHIFT_START,
                    end_time=LOP_SHIFT_END,
                    break_minutes=LOP_BREAK_MINUTES,
                )
                for day in LOP_WORKDAYS
            ],
            actor_email="seed@peoplepay360.com",
        )
        created["schedule"] = True

    # --- employee ---------------------------------------------------------
    employee = (
        await db.execute(
            select(Employee).where(
                Employee.work_email == LOP_EMPLOYEE_EMAIL,
                Employee.tenant_id == "default",
            )
        )
    ).scalar_one_or_none()
    if employee is None:
        engineering = (
            await db.execute(
                select(Department).where(
                    Department.code == "ENG", Department.tenant_id == "default"
                )
            )
        ).scalar_one_or_none()
        employee = await EmployeeRepository(db).create(
            Employee(
                public_id="temp",
                first_name="Lakshmi",
                last_name="Prasad",
                work_email=LOP_EMPLOYEE_EMAIL,
                job_position="Support Engineer",
                employee_type=EmployeeType.PERMANENT,
                department_id=engineering.id if engineering else None,
                default_schedule_id=schedule.id,
                # Present ON PURPOSE: without it every payslip for this
                # employee carries a blocking `missing_bank_details` warning
                # and the demo payrun can never be validated.
                bank_account="IN00PP360DEMO0001",
                hire_date=LOP_CONTRACT_START,
            )
        )
        created["employee"] = True

    # --- link the employee login to this Employee -------------------------
    # This is what makes PRD §3's Employee role demonstrable at all. The token
    # minted at login carries an `employee_id` claim only when some Employee
    # row points at the user (app/api/v1/routers/auth.py), and every "own
    # records only" rule compares against that claim rather than trusting a
    # request body. No endpoint sets `Employee.user_id` — `EmployeeCreate`
    # excludes it deliberately — so without this the only way to demo Employee
    # self-service is an UPDATE typed into psql, which PRD §7 rules out.
    if employee.user_id is None:
        login = (
            await db.execute(select(User).where(User.email == EMPLOYEE_LOGIN_EMAIL))
        ).scalar_one_or_none()
        if login is not None:
            already_linked = (
                await db.execute(
                    select(Employee.id).where(
                        Employee.user_id == login.id, Employee.id != employee.id
                    )
                )
            ).scalar_one_or_none()
            if already_linked is None:
                # `user_id` is UNIQUE; claiming a login another Employee already
                # holds would raise rather than silently move the account.
                employee.user_id = login.id
                created["employee_login_link"] = True
            else:
                logger.warning(
                    "Login %s is already linked to another Employee; leaving it "
                    "alone. Employee self-service will act as that person.",
                    EMPLOYEE_LOGIN_EMAIL,
                )

    # --- contract ---------------------------------------------------------
    contract = (
        await db.execute(
            select(Contract).where(
                Contract.employee_id == employee.id,
                Contract.status == ContractStatus.ACTIVE,
                Contract.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if contract is None:
        contract = await ContractRepository(db).create(
            Contract(
                public_id="temp",
                employee_id=employee.id,
                department_id=employee.department_id,
                job_position="Support Engineer",
                wage=LOP_CONTRACT_WAGE,
                salary_structure_id=structure.id,
                working_schedule_id=schedule.id,
                start_date=LOP_CONTRACT_START,
                end_date=None,
                status=ContractStatus.ACTIVE,
                notes="Demo contract for the Loss-of-Pay scenario.",
            )
        )
        created["contract"] = True

    # --- payroll-integrated unpaid leave type -----------------------------
    # `payroll_integration=True` is the switch that makes an approved absence
    # reach UNPAID_LEAVE_DAYS at all; `requires_allocation=False` means unpaid
    # leave needs no balance to draw from, which is what "unpaid" means.
    leave_type = (
        await db.execute(
            select(TimeOffType).where(
                TimeOffType.code == LOP_LEAVE_TYPE_CODE,
                TimeOffType.tenant_id == "default",
            )
        )
    ).scalar_one_or_none()
    if leave_type is None:
        leave_type = await TimeOffTypeRepository(db).create(
            TimeOffType(
                public_id="temp",
                name="Unpaid Leave (demo)",
                code=LOP_LEAVE_TYPE_CODE,
                unit=TimeOffUnit.DAYS,
                requires_allocation=False,
                requires_approval=True,
                payroll_integration=True,
                description="Unpaid absence; reaches payroll as UNPAID_LEAVE_DAYS.",
            )
        )
        created["leave_type"] = True

    # --- the approved absence --------------------------------------------
    request = (
        await db.execute(
            select(TimeOffRequest).where(
                TimeOffRequest.employee_id == employee.id,
                TimeOffRequest.time_off_type_id == leave_type.id,
                TimeOffRequest.date_from == LOP_LEAVE_FROM,
                TimeOffRequest.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if request is None:
        request = await TimeOffRequestRepository(db).create(
            TimeOffRequest(
                public_id="temp",
                employee_id=employee.id,
                time_off_type_id=leave_type.id,
                date_from=LOP_LEAVE_FROM,
                date_to=LOP_LEAVE_TO,
                # Phase 2's own duration function, not a hand-counted number:
                # duration is the amount a balance is debited by, and two
                # implementations of it would eventually disagree. This type
                # needs no allocation, so nothing is debited — but the stored
                # duration still has to mean what Phase 2 says it means.
                duration=request_duration(
                    employee, leave_type, LOP_LEAVE_FROM, LOP_LEAVE_TO
                ),
                # Approved directly, with `allocation_id` left NULL. That is
                # exactly the row `TimeOffRequestService._approve` produces for
                # a `requires_allocation=False` type — it skips the allocation
                # lookup entirely — so this is not a shortcut around the
                # service's rules, it is the same end state.
                status=TimeOffRequestStatus.APPROVED,
                allocation_id=None,
                reason="Unpaid leave for the Loss-of-Pay demo scenario.",
                decision_note="Approved by the demo seed.",
            )
        )
        created["leave_request"] = True

    # --- attendance over the period --------------------------------------
    # August, the LIVE demo period, and July, the period the seeded paid run
    # covers — so both read as genuinely worked. The approved unpaid leave is
    # skipped in August, which is what makes UNPAID_LEAVE_DAYS three.
    august_rows = await _seed_attendance_window(
        db,
        employee,
        schedule,
        LOP_PERIOD_START,
        LOP_PERIOD_END,
        skip=(LOP_LEAVE_FROM, LOP_LEAVE_TO),
    )
    if august_rows is not None:
        created["attendance_days"] = august_rows
    july_rows = await _seed_attendance_window(
        db, employee, schedule, DEMO_PAID_PERIOD_START, DEMO_PAID_PERIOD_END
    )
    if july_rows is not None:
        created["attendance_days_july"] = july_rows

    await db.commit()
    return {
        "employee": employee,
        "contract": contract,
        "schedule": schedule,
        "leave_request": request,
        "created": created,
    }


async def _seed_attendance_window(
    db,
    employee,
    schedule,
    start: date,
    end: date,
    *,
    skip: tuple[date, date] | None = None,
) -> int | None:
    """Complete check-in/check-out rows on every scheduled day in the window.

    Returns the number of rows written, or `None` when the window already has
    attendance and nothing was done — the caller reports "new this run"
    honestly, and a second seed cannot double a month.

    Complete check-outs on every day matter beyond tidiness: an open row makes
    `missing_checkout` fire, that finding is BLOCKING at the Validate firewall,
    and a demo dataset whose payrun cannot be validated is not a demo dataset.
    """
    existing = (
        await db.execute(
            select(Attendance.id)
            .where(
                Attendance.employee_id == employee.id,
                Attendance.check_in >= datetime.combine(start, time.min, UTC),
                Attendance.check_in <= datetime.combine(end, time.max, UTC),
                Attendance.deleted_at.is_(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return None

    workdays = set(LOP_WORKDAYS)
    day, rows = start, 0
    while day <= end:
        on_leave = skip is not None and skip[0] <= day <= skip[1]
        if _weekday_of(day) in workdays and not on_leave:
            check_in = datetime.combine(day, LOP_SHIFT_START, UTC)
            check_out = datetime.combine(day, LOP_ATTENDANCE_OUT, UTC)
            await AttendanceRepository(db).create(
                Attendance(
                    public_id="temp",
                    employee_id=employee.id,
                    check_in=check_in,
                    check_out=check_out,
                    worked_hours=worked_hours(check_in, check_out),
                    # Derived by Phase 2's own pure policy, with the schedule
                    # this contract actually names — never hardcoded, because
                    # reads re-derive it and a stored status that disagrees
                    # with the derivation is a lie waiting to be spotted.
                    status=_derived_status(employee, schedule, check_in, check_out),
                )
            )
            rows += 1
        day += timedelta(days=1)
    return rows


async def _seed_roster(db, structure, schedule) -> tuple[list, dict]:
    """The four non-LOP demo employees, each with a contract and attendance.

    Same idempotency discipline as `_seed_lop_scenario`: looked up by work
    email, and the contract by "does this employee already have an active
    one". A second active contract would not merely duplicate a row — it is
    the exact thing `contracts_active_period_overlap_excl` refuses, so a
    careless re-run would crash rather than duplicate.
    """
    employees, created = [], {"roster_employees": 0, "roster_contracts": 0}
    for first, last, email, dept_code, position, kind, wage in DEMO_ROSTER:
        employee = (
            await db.execute(
                select(Employee).where(
                    Employee.work_email == email, Employee.tenant_id == "default"
                )
            )
        ).scalar_one_or_none()
        if employee is None:
            department = (
                await db.execute(
                    select(Department).where(
                        Department.code == dept_code, Department.tenant_id == "default"
                    )
                )
            ).scalar_one_or_none()
            employee = await EmployeeRepository(db).create(
                Employee(
                    public_id="temp",
                    first_name=first,
                    last_name=last,
                    work_email=email,
                    job_position=position,
                    employee_type=kind,
                    department_id=department.id if department else None,
                    default_schedule_id=schedule.id,
                    bank_account=f"IN00PP360DEMO{len(employees) + 2:04d}",
                    hire_date=LOP_CONTRACT_START,
                )
            )
            created["roster_employees"] += 1

        contract = (
            await db.execute(
                select(Contract).where(
                    Contract.employee_id == employee.id,
                    Contract.status == ContractStatus.ACTIVE,
                    Contract.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if contract is None:
            await ContractRepository(db).create(
                Contract(
                    public_id="temp",
                    employee_id=employee.id,
                    department_id=employee.department_id,
                    job_position=position,
                    wage=wage,
                    salary_structure_id=structure.id,
                    working_schedule_id=schedule.id,
                    start_date=LOP_CONTRACT_START,
                    end_date=None,
                    status=ContractStatus.ACTIVE,
                    notes="Demo contract for the seeded roster.",
                )
            )
            created["roster_contracts"] += 1

        for start, end in (
            (DEMO_PAID_PERIOD_START, DEMO_PAID_PERIOD_END),
            (LOP_PERIOD_START, LOP_PERIOD_END),
        ):
            rows = await _seed_attendance_window(db, employee, schedule, start, end)
            if rows:
                created["roster_attendance_days"] = (
                    created.get("roster_attendance_days", 0) + rows
                )
        employees.append(employee)

    await db.commit()
    return employees, created


async def _seed_paid_payrun(db, structure, employees) -> dict:
    """A finalized July 2026 payrun — computed, validated and marked paid
    through the REAL service methods, then proved to render a real PDF.

    Every step goes through `PayrunService`, never through repositories or
    SQL, for the reason the module docstring gives: a payslip written by
    anything other than the compute engine has no `reference_snapshot`, and
    migration 018 makes exactly those rows answer 409 on every read — so a
    hand-built "paid" payslip would produce a demo dataset whose payslips
    cannot be opened, printed or emailed. Compute is the only writer.

    The PDF is RENDERED here rather than asserted about. PRD §11 asks the
    dataset to contain a payslip with a real generated PDF behind it, and the
    only honest way to know that is true is to generate one and look at the
    bytes. Nothing is stored: `/payslips/{id}/pdf` renders on demand from the
    persisted snapshot, and a cached file would be a second, staler copy of
    money that is supposed to have exactly one source.
    """
    outcome: dict = {"created": False}
    existing = (
        await db.execute(
            select(Payrun).where(
                Payrun.name == DEMO_PAID_PAYRUN_NAME,
                Payrun.tenant_id == "default",
                Payrun.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        outcome["payrun_id"] = existing.public_id
        outcome["status"] = existing.status.value
        return outcome

    service = PayrunService(db)
    payrun = await service.create_payrun(
        PayrunCreate(
            name=DEMO_PAID_PAYRUN_NAME,
            salary_structure_id=structure.public_id,
            period_start=DEMO_PAID_PERIOD_START,
            period_end=DEMO_PAID_PERIOD_END,
            notes="Seeded demo history: a finalized run whose payslips print.",
            employee_ids=[employee.public_id for employee in employees],
        ),
        actor_email=SEED_ACTOR,
    )
    await db.commit()

    computed = await service.compute(
        payrun.public_id, payrun.version, actor_email=SEED_ACTOR
    )
    await db.commit()
    payrun = computed["payrun"]
    if computed["blocking"]:
        # Loud rather than silent. A seeded run that cannot be validated is a
        # broken demo dataset, and the codes name exactly which input is wrong.
        logger.error(
            "Seeded July payrun has blocking findings and was left COMPUTED: %s. "
            "Fix the offending inputs and re-run the seed.",
            computed["blocking"],
        )
        outcome.update(
            payrun_id=payrun.public_id, status=payrun.status.value, created=True
        )
        return outcome

    validated = await service.validate_payrun(
        payrun.public_id, payrun.version, actor_email=SEED_ACTOR
    )
    await db.commit()
    payrun = validated["payrun"]
    payrun = await service.mark_paid(
        payrun.public_id, payrun.version, actor_email=SEED_ACTOR
    )
    await db.commit()

    payslips, _ = await PayslipService(db).list_payslips(
        payrun_id=payrun.public_id, limit=200, offset=0
    )
    outcome.update(
        created=True,
        payrun_id=payrun.public_id,
        status=payrun.status.value,
        payslips=len(payslips),
        pdf=await _verify_pdf(db, payslips),
    )
    return outcome


async def _verify_pdf(db, payslips) -> str:
    """Render one seeded payslip and report what actually came back.

    Never raises. The seed runs as the backend container's start command
    (`alembic upgrade head && python -m app.seed && uvicorn ...`), so an
    exception here would stop the whole application from starting over a
    document-rendering dependency. WeasyPrint needs native Pango/Cairo
    libraries and the DejaVu fonts at `PAYSLIP_FONT_DIR`; the image installs
    both, a bare host may well not. That is worth an explicit ERROR line
    naming the cause, and is not worth taking the API down for.
    """
    if not payslips:
        return "no payslip to render"
    try:
        pdf = await document_pdf(db, payslips[0].public_id)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed silently
        logger.error(
            "Payslip PDF rendering is UNAVAILABLE in this environment: %s: %s. "
            "The paid payrun and its payslips are correct; only the document "
            "renderer is missing (WeasyPrint native libraries / PAYSLIP_FONT_DIR).",
            type(exc).__name__,
            exc,
        )
        return f"unavailable ({type(exc).__name__})"
    if not pdf.startswith(b"%PDF-"):
        logger.error("Payslip renderer returned %d bytes that are not a PDF.", len(pdf))
        return "invalid"
    return f"{payslips[0].public_id} renders {len(pdf)} bytes"


def _weekday_of(day: date) -> Weekday:
    """`date.weekday()` (Mon=0) mapped onto the Weekday enum through
    WEEKDAY_ORDER, which already encodes that same ordering — rather than a
    second index table that could drift from it."""
    from app.models.enums import WEEKDAY_ORDER

    return next(name for name, index in WEEKDAY_ORDER.items() if index == day.weekday())


def _derived_status(employee, schedule, check_in, check_out):
    """Phase 2's pure status policy, against the contract's schedule."""
    expected_start, expected_hours = schedule_expectations(
        employee, check_in.date(), schedule=schedule
    )
    return derive_attendance_status(
        check_in,
        check_out,
        now=datetime.now(UTC),
        expected_start=expected_start,
        expected_hours=expected_hours,
    )


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        departments = await _seed_departments(db)
        users = await _seed_users(db)
        structure = await _seed_salary_structure(db)
        await db.commit()
        scenario = await _seed_lop_scenario(db, structure)
        roster, roster_created = await _seed_roster(
            db, structure, scenario["schedule"]
        )
        # The Loss-of-Pay employee is paid in July alongside everyone else;
        # only their August is left for the live demo.
        paid = await _seed_paid_payrun(
            db, structure, [scenario["employee"], *roster]
        )

    logger.info(
        "Seed complete: %d department(s), %d user(s) created.", departments, users
    )
    logger.info(
        "Demo salary structure: %s (includes Loss of Pay).", structure.public_id
    )
    logger.info("Demo logins (email / password / role):")
    for email, password, _, role in DEMO_USERS:
        logger.info("  %-38s %-14s %s", email, password, role.value)

    created = {**scenario["created"], **roster_created}
    logger.info(
        "Roster: %d employee(s) across %d department(s), all with a schedule, "
        "a bank account and an open-ended active contract.",
        len(roster) + 1,
        len(DEMO_DEPARTMENTS),
    )
    logger.info(
        "Loss-of-Pay scenario: employee %s, contract %s (wage %s), approved unpaid "
        "leave %s..%s.",
        scenario["employee"].public_id,
        scenario["contract"].public_id,
        LOP_CONTRACT_WAGE,
        LOP_LEAVE_FROM,
        LOP_LEAVE_TO,
    )
    logger.info(
        "  new this run: %s",
        ", ".join(f"{key}={value}" for key, value in created.items())
        or "nothing (already seeded)",
    )

    if paid["created"]:
        logger.info(
            "Paid payrun %s (%s): %s, %s payslip(s). PDF: %s.",
            paid["payrun_id"],
            DEMO_PAID_PAYRUN_NAME,
            paid["status"],
            paid.get("payslips", 0),
            paid.get("pdf", "not rendered"),
        )
    else:
        logger.info(
            "Paid payrun %s (%s) was already seeded; left untouched.",
            paid["payrun_id"],
            paid["status"],
        )

    logger.info(
        "  LIVE DEMO (PRD §7 scenario 1): create a payrun over %s..%s on structure "
        "PP360_DEMO, select %s, and Compute. Expect LOP 4285.71 and Net 37514.29, "
        "then Validate / Mark paid / Print / Send payslips.",
        LOP_PERIOD_START,
        LOP_PERIOD_END,
        scenario["employee"].public_id,
    )


if __name__ == "__main__":
    asyncio.run(seed())
