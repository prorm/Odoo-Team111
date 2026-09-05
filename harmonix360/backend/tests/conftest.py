"""Shared fixtures.

These tests run against a REAL Postgres (the same one alembic migrated), never
SQLite. The two behaviours worth the most here — the Contract non-overlap
EXCLUDE constraint and the transaction-scoped advisory lock — are properties of
the database's constraint system and lock manager; neither can be exercised
against SQLite, and neither can be proven by a mock.
"""
import uuid

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.main import app
from app.models.contract import Contract
from app.models.department import Department
from app.models.employee import Employee
from app.models.enums import UserRole
from app.models.payroll import Payrun, PayrunEmployee, Payslip, PayslipLine
from app.models.salary import SalaryRule, SalaryStructure, SalaryStructureRule
from app.models.working_schedule import ScheduleLine, WorkingSchedule
from app.repositories.hr import DepartmentRepository


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def session():
    async with AsyncSessionLocal() as s:
        yield s


def auth_headers(role: UserRole, *, employee_id: str | None = None) -> dict[str, str]:
    """A bearer token for `role`, optionally carrying an `employee_id` claim.

    Minted directly rather than obtained by logging in, so a role test does not
    also depend on the seed data existing. `test_auth_api.py` covers the login
    round-trip itself; here the token is a fixture, not the subject.
    """
    token = create_access_token(
        subject=f"{role.value}@peoplepay360.com", role=role.value, employee_id=employee_id
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def department():
    """A committed Department, torn down afterwards.

    Committed, not rolled back: an Employee FKs into it, and a row that exists
    only inside an uncommitted transaction is invisible to the separate session
    the ASGI client's request handler opens.
    """
    async with AsyncSessionLocal() as s:
        # Through the repository, so the row gets a real minted hashid. A
        # hand-written placeholder like "dept_test_7" does not decode, and the
        # service would 404 on it — which would look like a broken endpoint
        # rather than a broken fixture.
        dept = await DepartmentRepository(s).create(
            Department(public_id="temp", name="Test Engineering", code=f"TEST-{uuid.uuid4().hex[:6]}")
        )
        await s.commit()
        dept_id, public_id = dept.id, dept.public_id

    yield public_id

    async with AsyncSessionLocal() as s:
        # Clear the references first. pytest tears fixtures down in reverse
        # setup order, so this fixture is disposed BEFORE cleanup_employees —
        # and any employee or contract the test pointed at this department
        # would still be holding the FK.
        await s.execute(
            Employee.__table__.update().where(Employee.department_id == dept_id).values(department_id=None)
        )
        await s.execute(
            Contract.__table__.update().where(Contract.department_id == dept_id).values(department_id=None)
        )
        await s.execute(delete(Department).where(Department.id == dept_id))
        await s.commit()


@pytest_asyncio.fixture
async def cleanup_employees():
    """Deletes every Employee (and its Contracts) created during a test.

    Snapshots the maximum employee id up front and removes everything above it
    afterwards, rather than tracking ids the test created. Tests here create
    employees through the API, so the test itself does not always know the ids
    — and an untracked leftover would collide with the next run through the
    work_email unique constraint.
    """
    async with AsyncSessionLocal() as s:
        high_water = (await s.execute(select(Employee.id).order_by(Employee.id.desc()).limit(1))).scalar() or 0

    yield

    async with AsyncSessionLocal() as s:
        new_ids = (await s.execute(select(Employee.id).where(Employee.id > high_water))).scalars().all()
        if new_ids:
            from app.models.attendance import Attendance
            from app.models.time_off import TimeOffRequest, TimeOffAllocation
            # Payroll first: a Payslip FKs both the employee and their
            # contract, so it has to go before either. Defensive rather than
            # relying on `cleanup_payroll` having run — pytest disposes
            # fixtures in reverse setup order, and a test may list the two in
            # either order (same reasoning as cleanup_salary_config's note).
            doomed_payslips = (
                await s.execute(select(Payslip.id).where(Payslip.employee_id.in_(new_ids)))
            ).scalars().all()
            if doomed_payslips:
                await s.execute(delete(PayslipLine).where(PayslipLine.payslip_id.in_(doomed_payslips)))
                await s.execute(delete(Payslip).where(Payslip.id.in_(doomed_payslips)))
            await s.execute(delete(PayrunEmployee).where(PayrunEmployee.employee_id.in_(new_ids)))
            await s.execute(delete(TimeOffRequest).where(TimeOffRequest.employee_id.in_(new_ids)))
            await s.execute(delete(TimeOffAllocation).where(TimeOffAllocation.employee_id.in_(new_ids)))
            await s.execute(delete(Attendance).where(Attendance.employee_id.in_(new_ids)))
            await s.execute(delete(Contract).where(Contract.employee_id.in_(new_ids)))
            # Managers first would violate the self-FK, so clear it before
            # deleting anything.
            await s.execute(
                Employee.__table__.update().where(Employee.id.in_(new_ids)).values(manager_id=None)
            )
            await s.execute(delete(Employee).where(Employee.id.in_(new_ids)))
        await s.commit()


@pytest_asyncio.fixture
async def cleanup_schedules():
    async with AsyncSessionLocal() as s:
        high_water = (
            await s.execute(select(WorkingSchedule.id).order_by(WorkingSchedule.id.desc()).limit(1))
        ).scalar() or 0

    yield

    async with AsyncSessionLocal() as s:
        new_ids = (
            await s.execute(select(WorkingSchedule.id).where(WorkingSchedule.id > high_water))
        ).scalars().all()
        if new_ids:
            await s.execute(delete(ScheduleLine).where(ScheduleLine.schedule_id.in_(new_ids)))
            # Clear the references first — an Employee's default schedule and a
            # Contract's override both FK into here, and pytest may dispose this
            # fixture before the one that deletes those rows.
            await s.execute(
                Employee.__table__.update()
                .where(Employee.default_schedule_id.in_(new_ids))
                .values(default_schedule_id=None)
            )
            await s.execute(
                Contract.__table__.update()
                .where(Contract.working_schedule_id.in_(new_ids))
                .values(working_schedule_id=None)
            )
            await s.execute(delete(WorkingSchedule).where(WorkingSchedule.id.in_(new_ids)))
        await s.commit()


@pytest_asyncio.fixture
async def cleanup_salary_config():
    """Deletes every SalaryStructure/SalaryRule (and their link rows) created
    during a test. Same high-water-mark strategy as `cleanup_schedules` —
    tests create these through the API and don't always know the ids.

    Defensively nulls any Contract.salary_structure_id pointing at a
    structure being removed, regardless of whether `cleanup_employees` has
    already run: pytest tears fixtures down in reverse setup order, and a
    test may list these two fixtures in either order.
    """
    async with AsyncSessionLocal() as s:
        rule_high_water = (
            await s.execute(select(SalaryRule.id).order_by(SalaryRule.id.desc()).limit(1))
        ).scalar() or 0
        structure_high_water = (
            await s.execute(select(SalaryStructure.id).order_by(SalaryStructure.id.desc()).limit(1))
        ).scalar() or 0

    yield

    async with AsyncSessionLocal() as s:
        new_structure_ids = (
            await s.execute(select(SalaryStructure.id).where(SalaryStructure.id > structure_high_water))
        ).scalars().all()
        new_rule_ids = (
            await s.execute(select(SalaryRule.id).where(SalaryRule.id > rule_high_water))
        ).scalars().all()

        if new_structure_ids:
            await s.execute(
                Contract.__table__.update()
                .where(Contract.salary_structure_id.in_(new_structure_ids))
                .values(salary_structure_id=None)
            )
            await s.execute(
                delete(SalaryStructureRule).where(SalaryStructureRule.structure_id.in_(new_structure_ids))
            )
            await s.execute(delete(SalaryStructure).where(SalaryStructure.id.in_(new_structure_ids)))
        if new_rule_ids:
            await s.execute(
                delete(SalaryStructureRule).where(SalaryStructureRule.salary_rule_id.in_(new_rule_ids))
            )
            await s.execute(delete(SalaryRule).where(SalaryRule.id.in_(new_rule_ids)))
        await s.commit()


@pytest_asyncio.fixture
async def cleanup_payroll():
    """Deletes every Payrun (with its selection, payslips and payslip lines)
    created during a test.

    Same high-water-mark strategy as `cleanup_schedules` and
    `cleanup_salary_config`. Deleted in dependency order — lines, payslips,
    selection, run — because these are real foreign keys and Postgres will not
    take them in any other order.

    Payruns are deleted rather than soft-deleted here. A test's payrun must
    leave nothing behind: `uq_payslip_payrun_employee` and the
    `duplicate_payslip` warning both read rows regardless of `deleted_at`, so
    a tombstoned payslip from a previous run would change the NEXT run's
    warnings — a test failing because of what an earlier test left behind is
    the worst kind of flake to chase.
    """
    async with AsyncSessionLocal() as s:
        high_water = (await s.execute(select(Payrun.id).order_by(Payrun.id.desc()).limit(1))).scalar() or 0

    yield

    async with AsyncSessionLocal() as s:
        new_ids = (await s.execute(select(Payrun.id).where(Payrun.id > high_water))).scalars().all()
        if new_ids:
            payslip_ids = (
                await s.execute(select(Payslip.id).where(Payslip.payrun_id.in_(new_ids)))
            ).scalars().all()
            if payslip_ids:
                await s.execute(delete(PayslipLine).where(PayslipLine.payslip_id.in_(payslip_ids)))
                await s.execute(delete(Payslip).where(Payslip.id.in_(payslip_ids)))
            await s.execute(delete(PayrunEmployee).where(PayrunEmployee.payrun_id.in_(new_ids)))
            await s.execute(delete(Payrun).where(Payrun.id.in_(new_ids)))
        await s.commit()


def unique_email(prefix: str = "test") -> str:
    return f"{prefix}.{uuid.uuid4().hex[:10]}@peoplepay360.com"


@pytest_asyncio.fixture(autouse=True)
async def reset_rate_limit():
    """Clear the rate-limiter's buckets before every test.

    The limiter keys on (client IP, path, minute), and every test in this suite
    arrives from the same ASGI transport at the same path — so without this,
    a suite that exercises one endpoint thoroughly starts 429-ing partway
    through and the failures point at the endpoint rather than at the limiter.

    Clearing rather than disabling: the limiter stays in the request path, so a
    bug in it still surfaces here.
    """
    from app.core.redis import redis_client

    keys = [key async for key in redis_client.scan_iter("rate_limit:*")]
    if keys:
        await redis_client.delete(*keys)
    yield
