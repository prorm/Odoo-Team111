"""Seed a realistic, synthetic office roster without touching application code.

Run from ``harmonix360/backend``:

    # Preview only. This never writes to PostgreSQL.
    python -m scripts.seed_professional_office

    # Insert the default 60-person dataset in one transaction.
    python -m scripts.seed_professional_office --apply

    # Optional deterministic sizing/date controls (minimum 50 employees).
    python -m scripts.seed_professional_office --apply --count 75 --as-of 2026-09-06

The script is deliberately safe for shared development and demonstration
databases:

* all names, emails, phone numbers and bank references are synthetic;
* dry-run is the default and ``--apply`` is required for writes;
* every row has a stable natural key, so reruns do not duplicate data;
* existing non-seed employee records are never updated or deleted;
* the write is atomic -- any failure rolls back the whole import;
* contracts use the existing PP360_DEMO salary structure and preserve the
  database's active-contract overlap constraint;
* working hours and attendance hours are calculated with the application's
  existing domain helpers, rather than being independently reimplemented.

This is a data fixture, not an account-provisioning shortcut. It does not
create login credentials for the generated employees. User access should be
provisioned through the normal administrative workflow.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.attendance import Attendance
from app.models.contract import Contract
from app.models.department import Department
from app.models.employee import Employee
from app.models.enums import (
    ContractStatus,
    EmployeeStatus,
    EmployeeType,
    TimeOffAllocationStatus,
    TimeOffRequestStatus,
    TimeOffUnit,
    UserRole,
    UserStatus,
    Weekday,
    WorkingScheduleType,
)
from app.models.salary import SalaryStructure
from app.models.time_off import TimeOffAllocation, TimeOffRequest, TimeOffType
from app.models.user import User
from app.models.working_schedule import WorkingSchedule
from app.repositories.hr import (
    AttendanceRepository,
    ContractRepository,
    DepartmentRepository,
    EmployeeRepository,
    TimeOffAllocationRepository,
    TimeOffRequestRepository,
    TimeOffTypeRepository,
)
from app.schemas.schedule import ScheduleLineInput
from app.services.attendance import derive_attendance_status, worked_hours
from app.services.schedule import WorkingScheduleService

TENANT_ID = "default"
DATASET_MARKER = "PP360 professional office seed"
SEED_ACTOR = "seed@peoplepay360.com"
DEFAULT_EMPLOYEE_COUNT = 60
MIN_EMPLOYEE_COUNT = 50
MAX_EMPLOYEE_COUNT = 500
SALARY_STRUCTURE_CODE = "PP360_DEMO"
ANNUAL_LEAVE_CODE = "OFFICE_ANNUAL"

#: Synthetic addresses still have to be addresses the APPLICATION can read.
#:
#: This seed writes through the repositories, which skips the request schemas —
#: so nothing here validates the email on the way IN, but `EmployeeRef.work_email`
#: is an `EmailStr` and validates it on the way OUT. `@example.test` looks like
#: the safe choice and is not: `.test` is an IANA special-use TLD that
#: email-validator refuses, so every seeded row inserted cleanly and then 500'd
#: every list endpoint that embeds an employee (employees, contracts,
#: attendance, time-off). The domain must be one the reader accepts.
#:
#: `.invalid`, `.localhost` and `.example` fail for the same reason. The
#: `office.NNN.` prefix is what keeps these from colliding with app.seed's
#: named demo staff on the same domain.
EMAIL_DOMAIN = "peoplepay360.com"


@dataclass(frozen=True)
class DepartmentPlan:
    code: str
    name: str
    baseline_headcount: int
    leader_title: str
    positions: tuple[str, ...]
    salary_floor: int
    salary_ceiling: int
    schedule_key: str = "full_time"


@dataclass(frozen=True)
class SchedulePlan:
    name: str
    schedule_type: WorkingScheduleType
    start: time
    end: time
    break_minutes: int
    days: tuple[Weekday, ...]

    @property
    def paid_minutes(self) -> int:
        start_minutes = self.start.hour * 60 + self.start.minute
        end_minutes = self.end.hour * 60 + self.end.minute
        return end_minutes - start_minutes - self.break_minutes


@dataclass(frozen=True)
class PersonSpec:
    ordinal: int
    first_name: str
    last_name: str
    email: str
    department_code: str
    department_index: int
    job_position: str
    employee_type: EmployeeType
    status: EmployeeStatus
    schedule_key: str
    hire_date: date
    wage: Decimal


WEEKDAYS = (
    Weekday.MONDAY,
    Weekday.TUESDAY,
    Weekday.WEDNESDAY,
    Weekday.THURSDAY,
    Weekday.FRIDAY,
)

SCHEDULES: dict[str, SchedulePlan] = {
    "full_time": SchedulePlan(
        name="PP360 Office Standard (09:00-18:00)",
        schedule_type=WorkingScheduleType.FULL_TIME,
        start=time(9, 0),
        end=time(18, 0),
        break_minutes=60,
        days=WEEKDAYS,
    ),
    "flexible": SchedulePlan(
        name="PP360 Office Flexible (10:00-19:00)",
        schedule_type=WorkingScheduleType.FLEXIBLE,
        start=time(10, 0),
        end=time(19, 0),
        break_minutes=60,
        days=WEEKDAYS,
    ),
    "part_time": SchedulePlan(
        name="PP360 Office Part-Time (09:00-14:00)",
        schedule_type=WorkingScheduleType.PART_TIME,
        start=time(9, 0),
        end=time(14, 0),
        break_minutes=0,
        days=WEEKDAYS,
    ),
}

DEPARTMENTS: tuple[DepartmentPlan, ...] = (
    DepartmentPlan(
        "EXEC",
        "Executive Office",
        3,
        "Chief Executive Officer",
        ("Chief Operating Officer", "Executive Business Partner"),
        140_000,
        350_000,
        "flexible",
    ),
    DepartmentPlan(
        "ENG",
        "Engineering",
        18,
        "VP Engineering",
        (
            "Engineering Manager",
            "Principal Software Engineer",
            "Senior Software Engineer",
            "Software Engineer",
            "Quality Engineer",
            "Site Reliability Engineer",
            "Data Engineer",
        ),
        55_000,
        240_000,
        "flexible",
    ),
    DepartmentPlan(
        "PROD",
        "Product",
        7,
        "Director of Product",
        (
            "Senior Product Manager",
            "Product Manager",
            "Product Designer",
            "Business Analyst",
            "UX Researcher",
        ),
        50_000,
        210_000,
        "flexible",
    ),
    DepartmentPlan(
        "SALES",
        "Sales",
        10,
        "VP Sales",
        (
            "Regional Sales Manager",
            "Enterprise Account Executive",
            "Account Executive",
            "Sales Development Representative",
            "Sales Operations Analyst",
        ),
        42_000,
        220_000,
    ),
    DepartmentPlan(
        "CS",
        "Customer Success",
        7,
        "Director of Customer Success",
        (
            "Customer Success Manager",
            "Implementation Consultant",
            "Customer Support Specialist",
            "Technical Account Manager",
        ),
        40_000,
        180_000,
    ),
    DepartmentPlan(
        "FIN",
        "Finance",
        5,
        "Finance Director",
        (
            "Finance Manager",
            "Senior Financial Analyst",
            "Accountant",
            "Payroll Analyst",
        ),
        48_000,
        210_000,
    ),
    DepartmentPlan(
        "HR",
        "People Operations",
        5,
        "Head of People",
        (
            "HR Business Partner",
            "Talent Acquisition Specialist",
            "People Operations Specialist",
            "Learning and Development Partner",
        ),
        45_000,
        190_000,
    ),
    DepartmentPlan(
        "OPS",
        "Business Operations",
        5,
        "Director of Operations",
        (
            "Operations Manager",
            "Procurement Specialist",
            "Workplace Coordinator",
            "Operations Analyst",
        ),
        38_000,
        180_000,
    ),
)

FIRST_NAMES = (
    "Aarav",
    "Aditi",
    "Aditya",
    "Akash",
    "Amara",
    "Ananya",
    "Anika",
    "Arjun",
    "Avni",
    "Dev",
    "Diya",
    "Farhan",
    "Gauri",
    "Harish",
    "Ira",
    "Ishaan",
    "Jaya",
    "Kabir",
    "Kavya",
    "Kiran",
    "Krishna",
    "Lakshmi",
    "Maya",
    "Meera",
    "Mihir",
    "Naina",
    "Neha",
    "Nikhil",
    "Nisha",
    "Pranav",
    "Priya",
    "Rahul",
    "Rhea",
    "Rohan",
    "Saanvi",
    "Sameer",
    "Sanjay",
    "Shreya",
    "Tara",
    "Varun",
)

LAST_NAMES = (
    "Agarwal",
    "Bansal",
    "Bose",
    "Chandra",
    "Desai",
    "Fernandes",
    "Gupta",
    "Iyer",
    "Jain",
    "Joshi",
    "Kapoor",
    "Khan",
    "Kulkarni",
    "Malhotra",
    "Mehta",
    "Menon",
    "Mishra",
    "Mukherjee",
    "Nair",
    "Patel",
    "Prasad",
    "Rao",
    "Reddy",
    "Roy",
    "Saxena",
    "Sen",
    "Shah",
    "Sharma",
    "Singh",
    "Sinha",
    "Sood",
    "Srinivasan",
    "Subramanian",
    "Thomas",
    "Tripathi",
    "Varma",
    "Verma",
    "Vyas",
    "Yadav",
    "Zacharia",
)


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preview or insert a deterministic professional-office demo dataset."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="commit inserts; without this flag the command is read-only",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_EMPLOYEE_COUNT,
        help=f"employee count ({MIN_EMPLOYEE_COUNT}-{MAX_EMPLOYEE_COUNT}; default: {DEFAULT_EMPLOYEE_COUNT})",
    )
    parser.add_argument(
        "--as-of",
        type=parse_date,
        default=datetime.now(UTC).date(),
        help="reference date for contracts, leave and attendance (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--attendance-days",
        type=int,
        default=10,
        help="recent completed weekdays per employee (0-31; default: 10)",
    )
    args = parser.parse_args()
    if not MIN_EMPLOYEE_COUNT <= args.count <= MAX_EMPLOYEE_COUNT:
        parser.error(
            f"--count must be between {MIN_EMPLOYEE_COUNT} and {MAX_EMPLOYEE_COUNT}"
        )
    if not 0 <= args.attendance_days <= 31:
        parser.error("--attendance-days must be between 0 and 31")
    return args


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", ".", value.lower()).strip(".")


def allocate_headcount(total: int) -> dict[str, int]:
    baseline_total = sum(plan.baseline_headcount for plan in DEPARTMENTS)
    exact = {
        plan.code: Decimal(total * plan.baseline_headcount) / Decimal(baseline_total)
        for plan in DEPARTMENTS
    }
    counts = {plan.code: max(1, int(exact[plan.code])) for plan in DEPARTMENTS}
    counts["EXEC"] = max(3, counts["EXEC"])

    while sum(counts.values()) < total:
        candidate = max(
            DEPARTMENTS,
            key=lambda plan: (
                exact[plan.code] - counts[plan.code],
                plan.baseline_headcount,
            ),
        )
        counts[candidate.code] += 1
    while sum(counts.values()) > total:
        candidate = max(
            (
                plan
                for plan in DEPARTMENTS
                if counts[plan.code] > (3 if plan.code == "EXEC" else 1)
            ),
            key=lambda plan: (counts[plan.code] - exact[plan.code], counts[plan.code]),
        )
        counts[candidate.code] -= 1
    return counts


def employee_type_for(ordinal: int, *, is_leader: bool) -> EmployeeType:
    if is_leader:
        return EmployeeType.PERMANENT
    if ordinal % 19 == 0:
        return EmployeeType.INTERN
    if ordinal % 17 == 0:
        return EmployeeType.PART_TIME
    if ordinal % 13 == 0:
        return EmployeeType.CONTRACT
    return EmployeeType.PERMANENT


def employee_status_for(ordinal: int) -> EmployeeStatus:
    if ordinal % 29 == 0:
        return EmployeeStatus.NOTICE_PERIOD
    if ordinal % 23 == 0:
        return EmployeeStatus.ON_LEAVE
    return EmployeeStatus.ACTIVE


def wage_for(plan: DepartmentPlan, index: int, headcount: int, ordinal: int) -> Decimal:
    if index == 0:
        amount = plan.salary_ceiling
    else:
        rng = random.Random(36_000 + ordinal)
        seniority = Decimal(max(headcount - index, 1)) / Decimal(max(headcount, 1))
        spread = Decimal(plan.salary_ceiling - plan.salary_floor)
        amount = Decimal(plan.salary_floor) + spread * seniority * Decimal("0.58")
        amount += Decimal(rng.randrange(0, 8)) * Decimal(2500)
    rounded = (amount / Decimal(500)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return (rounded * Decimal(500)).quantize(Decimal("0.01"))


def build_people(total: int, as_of: date) -> list[PersonSpec]:
    counts = allocate_headcount(total)
    people: list[PersonSpec] = []
    ordinal = 1
    for plan in DEPARTMENTS:
        for department_index in range(counts[plan.code]):
            first = FIRST_NAMES[(ordinal - 1) % len(FIRST_NAMES)]
            last_offset = (ordinal - 1) * 11 + (ordinal - 1) // len(FIRST_NAMES)
            last = LAST_NAMES[last_offset % len(LAST_NAMES)]
            is_leader = department_index == 0
            employee_type = employee_type_for(ordinal, is_leader=is_leader)
            schedule_key = (
                "part_time"
                if employee_type in {EmployeeType.PART_TIME, EmployeeType.INTERN}
                else plan.schedule_key
            )
            position = (
                plan.leader_title
                if is_leader
                else plan.positions[(department_index - 1) % len(plan.positions)]
            )
            rng = random.Random(72_000 + ordinal)
            tenure_days = rng.randint(120, 2_600)
            hire_date = as_of - timedelta(days=tenure_days)
            email = f"office.{ordinal:03d}.{slug(first)}.{slug(last)}@{EMAIL_DOMAIN}"
            people.append(
                PersonSpec(
                    ordinal=ordinal,
                    first_name=first,
                    last_name=last,
                    email=email,
                    department_code=plan.code,
                    department_index=department_index,
                    job_position=position,
                    employee_type=employee_type,
                    status=employee_status_for(ordinal),
                    schedule_key=schedule_key,
                    hire_date=hire_date,
                    wage=wage_for(plan, department_index, counts[plan.code], ordinal),
                )
            )
            ordinal += 1
    return people


def previous_business_days(as_of: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = as_of
    while len(days) < count:
        cursor -= timedelta(days=1)
        if cursor.weekday() < 5:
            days.append(cursor)
    return sorted(days)


def next_business_day(day: date) -> date:
    cursor = day
    while cursor.weekday() >= 5:
        cursor += timedelta(days=1)
    return cursor


async def fetch_prerequisites(db) -> tuple[SalaryStructure | None, User | None]:
    structure = (
        await db.execute(
            select(SalaryStructure).where(
                SalaryStructure.code == SALARY_STRUCTURE_CODE,
                SalaryStructure.tenant_id == TENANT_ID,
                SalaryStructure.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    admin = (
        (
            await db.execute(
                select(User)
                .where(
                    User.role == UserRole.ADMIN,
                    User.status == UserStatus.ACTIVE,
                    User.deleted_at.is_(None),
                )
                .order_by(User.id)
            )
        )
        .scalars()
        .first()
    )
    return structure, admin


async def preview(
    db, people: list[PersonSpec], attendance_days: int
) -> dict[str, object]:
    emails = [person.email for person in people]
    existing = (
        (
            await db.execute(
                select(Employee.work_email).where(
                    Employee.work_email.in_(emails),
                    Employee.tenant_id == TENANT_ID,
                )
            )
        )
        .scalars()
        .all()
    )
    structure, admin = await fetch_prerequisites(db)
    return {
        "mode": "dry-run",
        "database_writes": False,
        "tenant": TENANT_ID,
        "planned_employees": len(people),
        "already_present": len(existing),
        "employees_to_insert": len(people) - len(existing),
        "planned_departments": [plan.code for plan in DEPARTMENTS],
        "planned_working_schedules": len(SCHEDULES),
        "planned_contracts": len(people),
        "planned_leave_allocations": len(people),
        "planned_attendance_rows_max": len(people) * attendance_days,
        "salary_structure_ready": structure is not None,
        "admin_approver_ready": admin is not None,
        "next_command": "python -m scripts.seed_professional_office --apply",
    }


async def ensure_departments(db) -> tuple[dict[str, Department], int]:
    rows: dict[str, Department] = {}
    created = 0
    for plan in DEPARTMENTS:
        row = (
            await db.execute(
                select(Department).where(
                    Department.code == plan.code,
                    Department.tenant_id == TENANT_ID,
                )
            )
        ).scalar_one_or_none()
        if row is not None and row.deleted_at is not None:
            raise RuntimeError(f"department {plan.code} exists but is archived")
        if row is None:
            row = await DepartmentRepository(db).create(
                Department(
                    public_id="temp",
                    name=plan.name,
                    code=plan.code,
                    tenant_id=TENANT_ID,
                )
            )
            created += 1
        rows[plan.code] = row
    return rows, created


async def ensure_schedules(db) -> tuple[dict[str, WorkingSchedule], int]:
    rows: dict[str, WorkingSchedule] = {}
    created = 0
    service = WorkingScheduleService(db)
    for key, plan in SCHEDULES.items():
        row = (
            await db.execute(
                select(WorkingSchedule).where(
                    WorkingSchedule.name == plan.name,
                    WorkingSchedule.tenant_id == TENANT_ID,
                )
            )
        ).scalar_one_or_none()
        if row is not None and row.deleted_at is not None:
            raise RuntimeError(f"working schedule {plan.name!r} exists but is archived")
        if row is None:
            row = await service.create_schedule(
                name=plan.name,
                schedule_type=plan.schedule_type,
                lines=[
                    ScheduleLineInput(
                        day_of_week=day,
                        start_time=plan.start,
                        end_time=plan.end,
                        break_minutes=plan.break_minutes,
                    )
                    for day in plan.days
                ],
                actor_email=SEED_ACTOR,
            )
            created += 1
        rows[key] = row
    return rows, created


async def ensure_leave_type(db) -> tuple[TimeOffType, int]:
    row = (
        await db.execute(
            select(TimeOffType).where(
                TimeOffType.code == ANNUAL_LEAVE_CODE,
                TimeOffType.tenant_id == TENANT_ID,
            )
        )
    ).scalar_one_or_none()
    if row is not None and row.deleted_at is not None:
        raise RuntimeError(f"time-off type {ANNUAL_LEAVE_CODE} exists but is archived")
    if row is not None:
        return row, 0
    row = await TimeOffTypeRepository(db).create(
        TimeOffType(
            public_id="temp",
            name="Annual Leave",
            code=ANNUAL_LEAVE_CODE,
            unit=TimeOffUnit.DAYS,
            requires_allocation=True,
            requires_approval=True,
            payroll_integration=False,
            description=f"Synthetic annual-leave policy created by {DATASET_MARKER}.",
            tenant_id=TENANT_ID,
        )
    )
    return row, 1


async def ensure_employees(
    db,
    people: list[PersonSpec],
    departments: dict[str, Department],
    schedules: dict[str, WorkingSchedule],
) -> tuple[dict[str, Employee], int]:
    emails = [person.email for person in people]
    existing_rows = (
        (
            await db.execute(
                select(Employee).where(
                    Employee.work_email.in_(emails),
                    Employee.tenant_id == TENANT_ID,
                )
            )
        )
        .scalars()
        .all()
    )
    employees = {row.work_email: row for row in existing_rows}
    if any(row.deleted_at is not None for row in existing_rows):
        archived = [
            row.work_email for row in existing_rows if row.deleted_at is not None
        ]
        raise RuntimeError(
            f"seed employee emails are archived: {', '.join(archived[:5])}"
        )

    created = 0
    department_heads: dict[str, Employee] = {}
    ceo: Employee | None = None
    for person in people:
        employee = employees.get(person.email)
        if employee is None:
            if person.department_code == "EXEC":
                manager_id = (
                    None if person.department_index == 0 else (ceo.id if ceo else None)
                )
            elif person.department_index == 0:
                manager_id = ceo.id if ceo else None
            else:
                manager_id = department_heads[person.department_code].id

            employee = await EmployeeRepository(db).create(
                Employee(
                    public_id="temp",
                    first_name=person.first_name,
                    last_name=person.last_name,
                    work_email=person.email,
                    phone=f"+91-00000-{person.ordinal:05d}",
                    department_id=departments[person.department_code].id,
                    manager_id=manager_id,
                    job_position=person.job_position,
                    employee_type=person.employee_type,
                    status=person.status,
                    default_schedule_id=schedules[person.schedule_key].id,
                    hire_date=person.hire_date,
                    bank_account=f"PP360-DEMO-{person.ordinal:08d}",
                    tenant_id=TENANT_ID,
                )
            )
            employees[person.email] = employee
            created += 1

        if person.department_index == 0:
            department_heads[person.department_code] = employee
            if person.department_code == "EXEC":
                ceo = employee

    return employees, created


async def ensure_contracts(
    db,
    people: list[PersonSpec],
    employees: dict[str, Employee],
    departments: dict[str, Department],
    schedules: dict[str, WorkingSchedule],
    structure: SalaryStructure,
    as_of: date,
) -> int:
    employee_ids = [employee.id for employee in employees.values()]
    existing_ids = set(
        (
            await db.execute(
                select(Contract.employee_id).where(
                    Contract.employee_id.in_(employee_ids),
                    Contract.status == ContractStatus.ACTIVE,
                    Contract.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    created = 0
    for person in people:
        employee = employees[person.email]
        if employee.id in existing_ids:
            continue
        end_date = None
        if person.employee_type == EmployeeType.CONTRACT:
            end_date = as_of + timedelta(days=365)
        elif person.employee_type == EmployeeType.INTERN:
            end_date = as_of + timedelta(days=180)
        await ContractRepository(db).create(
            Contract(
                public_id="temp",
                employee_id=employee.id,
                department_id=departments[person.department_code].id,
                job_position=person.job_position,
                wage=person.wage,
                salary_structure_id=structure.id,
                working_schedule_id=schedules[person.schedule_key].id,
                start_date=person.hire_date,
                end_date=end_date,
                status=ContractStatus.ACTIVE,
                notes=f"Synthetic employment terms created by {DATASET_MARKER}.",
                tenant_id=TENANT_ID,
            )
        )
        created += 1
    return created


async def ensure_leave_data(
    db,
    people: list[PersonSpec],
    employees: dict[str, Employee],
    leave_type: TimeOffType,
    admin: User,
    as_of: date,
) -> tuple[int, int]:
    year_start = date(as_of.year, 1, 1)
    year_end = date(as_of.year, 12, 31)
    employee_ids = [employee.id for employee in employees.values()]
    allocation_rows = (
        (
            await db.execute(
                select(TimeOffAllocation).where(
                    TimeOffAllocation.employee_id.in_(employee_ids),
                    TimeOffAllocation.time_off_type_id == leave_type.id,
                    TimeOffAllocation.valid_from == year_start,
                    TimeOffAllocation.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    allocations = {row.employee_id: row for row in allocation_rows}

    approved_candidates = [
        day for day in previous_business_days(as_of, 16) if day.year == as_of.year
    ]
    approved_day = approved_candidates[0] if approved_candidates else None
    pending_day = next_business_day(as_of + timedelta(days=14))
    request_dates = [pending_day]
    if approved_day is not None:
        request_dates.append(approved_day)
    request_rows = (
        (
            await db.execute(
                select(TimeOffRequest).where(
                    TimeOffRequest.employee_id.in_(employee_ids),
                    TimeOffRequest.time_off_type_id == leave_type.id,
                    TimeOffRequest.date_from.in_(request_dates),
                    TimeOffRequest.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    request_keys = {(row.employee_id, row.date_from) for row in request_rows}

    allocations_created = 0
    requests_created = 0
    for person in people:
        employee = employees[person.email]
        allocation = allocations.get(employee.id)
        if allocation is None:
            allocation = await TimeOffAllocationRepository(db).create(
                TimeOffAllocation(
                    public_id="temp",
                    employee_id=employee.id,
                    time_off_type_id=leave_type.id,
                    allocated=Decimal("24.00"),
                    taken=Decimal("0.00"),
                    valid_from=year_start,
                    valid_to=year_end,
                    status=TimeOffAllocationStatus.CONFIRMED,
                    tenant_id=TENANT_ID,
                )
            )
            allocations[employee.id] = allocation
            allocations_created += 1

        if (
            approved_day is not None
            and person.ordinal % 10 == 0
            and (employee.id, approved_day) not in request_keys
        ):
            if allocation.remaining < Decimal("1.00"):
                raise RuntimeError(f"insufficient annual leave for {person.email}")
            allocation.taken = (allocation.taken + Decimal("1.00")).quantize(
                Decimal("0.01")
            )
            await TimeOffRequestRepository(db).create(
                TimeOffRequest(
                    public_id="temp",
                    employee_id=employee.id,
                    time_off_type_id=leave_type.id,
                    date_from=approved_day,
                    date_to=approved_day,
                    duration=Decimal("1.00"),
                    status=TimeOffRequestStatus.APPROVED,
                    reason="Planned personal leave",
                    approved_by=admin.id,
                    decision_note=f"Approved sample request from {DATASET_MARKER}.",
                    allocation_id=allocation.id,
                    tenant_id=TENANT_ID,
                )
            )
            requests_created += 1

        if person.ordinal % 12 == 0 and (employee.id, pending_day) not in request_keys:
            await TimeOffRequestRepository(db).create(
                TimeOffRequest(
                    public_id="temp",
                    employee_id=employee.id,
                    time_off_type_id=leave_type.id,
                    date_from=pending_day,
                    date_to=pending_day,
                    duration=Decimal("1.00"),
                    status=TimeOffRequestStatus.TO_APPROVE,
                    reason="Planned personal appointment",
                    tenant_id=TENANT_ID,
                )
            )
            requests_created += 1
    return allocations_created, requests_created


async def ensure_attendance(
    db,
    people: list[PersonSpec],
    employees: dict[str, Employee],
    attendance_days: int,
    as_of: date,
) -> int:
    if attendance_days == 0:
        return 0
    days = previous_business_days(as_of, attendance_days)
    range_start = datetime.combine(days[0], time.min, UTC)
    range_end = datetime.combine(days[-1] + timedelta(days=1), time.min, UTC)
    employee_ids = [employee.id for employee in employees.values()]
    existing_rows = (
        await db.execute(
            select(Attendance.employee_id, Attendance.check_in).where(
                Attendance.employee_id.in_(employee_ids),
                Attendance.check_in >= range_start,
                Attendance.check_in < range_end,
                Attendance.deleted_at.is_(None),
            )
        )
    ).all()
    existing_keys = {
        (employee_id, check_in.astimezone(UTC).date())
        for employee_id, check_in in existing_rows
    }

    created = 0
    for person in people:
        employee = employees[person.email]
        plan = SCHEDULES[person.schedule_key]
        expected_hours = (Decimal(plan.paid_minutes) / Decimal(60)).quantize(
            Decimal("0.01")
        )
        for day_index, attendance_day in enumerate(days):
            if (employee.id, attendance_day) in existing_keys:
                continue
            expected_start = datetime.combine(attendance_day, plan.start, UTC)
            pattern = (person.ordinal + day_index) % 10
            delay_minutes = 15 if pattern == 0 else 0
            overtime_minutes = 60 if pattern == 1 else 0
            check_in = expected_start + timedelta(minutes=delay_minutes)
            check_out = check_in + timedelta(
                minutes=plan.paid_minutes + overtime_minutes
            )
            status = derive_attendance_status(
                check_in,
                check_out,
                now=datetime.combine(attendance_day + timedelta(days=1), time.min, UTC),
                expected_start=expected_start,
                expected_hours=expected_hours,
            )
            await AttendanceRepository(db).create(
                Attendance(
                    public_id="temp",
                    employee_id=employee.id,
                    check_in=check_in,
                    check_out=check_out,
                    worked_hours=worked_hours(check_in, check_out),
                    status=status,
                    tenant_id=TENANT_ID,
                )
            )
            created += 1
    return created


async def apply_seed(
    db,
    people: list[PersonSpec],
    attendance_days: int,
    as_of: date,
) -> dict[str, object]:
    structure, admin = await fetch_prerequisites(db)
    if structure is None or admin is None:
        missing: list[str] = []
        if structure is None:
            missing.append(f"salary structure {SALARY_STRUCTURE_CODE}")
        if admin is None:
            missing.append("an active admin user")
        raise RuntimeError(
            f"missing prerequisite: {', '.join(missing)}. Run `python -m app.seed` first."
        )

    departments, departments_created = await ensure_departments(db)
    schedules, schedules_created = await ensure_schedules(db)
    leave_type, leave_types_created = await ensure_leave_type(db)
    employees, employees_created = await ensure_employees(
        db, people, departments, schedules
    )
    contracts_created = await ensure_contracts(
        db, people, employees, departments, schedules, structure, as_of
    )
    allocations_created, requests_created = await ensure_leave_data(
        db, people, employees, leave_type, admin, as_of
    )
    attendance_created = await ensure_attendance(
        db, people, employees, attendance_days, as_of
    )
    return {
        "mode": "apply",
        "committed": True,
        "tenant": TENANT_ID,
        "target_employee_count": len(people),
        "created": {
            "departments": departments_created,
            "working_schedules": schedules_created,
            "time_off_types": leave_types_created,
            "employees": employees_created,
            "contracts": contracts_created,
            "leave_allocations": allocations_created,
            "leave_requests": requests_created,
            "attendance_rows": attendance_created,
        },
        "skipped_existing_employees": len(people) - employees_created,
        "salary_structure": SALARY_STRUCTURE_CODE,
        "dataset_marker": DATASET_MARKER,
    }


async def run(args: argparse.Namespace) -> None:
    people = build_people(args.count, args.as_of)
    async with AsyncSessionLocal() as db:
        if not args.apply:
            result = await preview(db, people, args.attendance_days)
            await db.rollback()
            print(json.dumps(result, indent=2, default=str))
            return

        try:
            result = await apply_seed(db, people, args.attendance_days, args.as_of)
            await db.commit()
        except Exception:
            await db.rollback()
            raise
        print(json.dumps(result, indent=2, default=str))


def main() -> None:
    args = parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()