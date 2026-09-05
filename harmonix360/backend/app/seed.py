"""Seed the demo dataset.

Idempotent by design — running it twice must not create a second copy of
anything, because it is run by hand during development and again right before
a demo, and a duplicated Employee is exactly the kind of thing that only
surfaces on stage.

    python -m app.seed

Seeds the identity layer, a demo salary structure including Loss of Pay, and
the INPUTS for a Loss-of-Pay payroll scenario. One login per role from PRD §3
plus the departments the HR domain hangs off.

WHAT THIS SEED DELIBERATELY DOES NOT CREATE
-------------------------------------------
No Payrun, Payslip or PayslipLine — not as an oversight, but because a payslip
written by anything other than the compute engine is a payslip with no
`reference_snapshot`, and migration 018 makes exactly those rows return 409
`historical_snapshot_unavailable` on every read (see progress.md's gap-fix
section). The seed lays out the inputs; a human presses Compute, and the
engine writes the payslip properly.

    TODO (Phase 5, PS B8): "a paid payrun with a generated PDF payslip" is
    part of the demo dataset the acceptance criteria ask for, and it is NOT
    seeded here. It cannot be: there is no PDF renderer in this tree, and
    faking one — a placeholder file, a payslip row with a `pdf_url` nothing
    produced — would make a missing feature look present. When
    `origin/phase-5-payslip-pdf-email` lands, extend `seed_lop_scenario`
    to compute → validate → mark paid → send, through the real endpoints.

THE SCENARIO, AND WHY THESE NUMBERS
-----------------------------------
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
from app.schemas.salary import SalaryRuleCreate, SalaryStructureCreate
from app.schemas.schedule import ScheduleLineInput
from app.models.working_schedule import WorkingSchedule
from app.services.attendance import (
    derive_attendance_status,
    schedule_expectations,
    worked_hours,
)
from app.services.salary import SalaryRuleService, SalaryStructureService
from app.services.schedule import WorkingScheduleService
from app.services.time_off import request_duration

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("peoplepay360.seed")

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
        "employee@peoplepay360.com",
        "employee123",
        "Rahul Verma (Employee)",
        UserRole.EMPLOYEE,
    ),
]

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
                created += 0
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
    # Only if none exists yet for this employee in the window. Complete
    # check-outs on every scheduled day except the approved leave, so the
    # period reads as genuinely worked and neither `missing_checkout` (a
    # blocking warning) nor `no_attendance` (advisory) fires.
    existing_attendance = (
        await db.execute(
            select(Attendance.id)
            .where(
                Attendance.employee_id == employee.id,
                Attendance.check_in >= datetime.combine(LOP_PERIOD_START, time.min, UTC),
                Attendance.check_in
                <= datetime.combine(LOP_PERIOD_END, time.max, UTC),
                Attendance.deleted_at.is_(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing_attendance is None:
        workdays = set(LOP_WORKDAYS)
        day = LOP_PERIOD_START
        rows = 0
        while day <= LOP_PERIOD_END:
            on_leave = LOP_LEAVE_FROM <= day <= LOP_LEAVE_TO
            if _weekday_of(day) in workdays and not on_leave:
                check_in = datetime.combine(day, LOP_SHIFT_START, UTC)
                check_out = datetime.combine(day, LOP_ATTENDANCE_OUT, UTC)
                row = Attendance(
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
                await AttendanceRepository(db).create(row)
                rows += 1
            day += timedelta(days=1)
        created["attendance_days"] = rows

    await db.commit()
    return {
        "employee": employee,
        "contract": contract,
        "schedule": schedule,
        "leave_request": request,
        "created": created,
    }


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

    logger.info(
        "Seed complete: %d department(s), %d user(s) created.", departments, users
    )
    logger.info(
        "Demo salary structure: %s (includes Loss of Pay).", structure.public_id
    )
    logger.info("Demo logins (email / password / role):")
    for email, password, _, role in DEMO_USERS:
        logger.info("  %-38s %-14s %s", email, password, role.value)

    created = scenario["created"]
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
        ", ".join(f"{key}={value}" for key, value in created.items()) or "nothing (already seeded)",
    )
    logger.info(
        "  To see it: create a payrun over %s..%s on structure PP360_DEMO, select "
        "%s, and Compute. Expect LOP 4285.71 and Net 37514.29.",
        LOP_PERIOD_START,
        LOP_PERIOD_END,
        scenario["employee"].public_id,
    )
    logger.info(
        "  NOT SEEDED (TODO, pending Phase 5 / PS B8): a paid payrun with a "
        "generated PDF payslip. No PDF renderer exists in this tree; the seed "
        "does not fake one. See the module docstring."
    )


if __name__ == "__main__":
    asyncio.run(seed())
