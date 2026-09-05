"""The Contract non-overlap EXCLUDE constraint (Architecture §6, PRD A2).

This is the single most important integrity guarantee in PeoplePay360: payroll
must resolve EXACTLY ONE contract for the period being run, and if it ever
resolves two, every downstream number is wrong in a way no later validation can
detect.

Every test here goes through a REAL Postgres INSERT and asserts on the
SQLSTATE Postgres itself raises. That is deliberate and non-negotiable — an
application-level "does an overlapping contract already exist?" check passes in
two concurrent requests simultaneously, and both commit. Testing the check
instead of the constraint would prove nothing about the failure mode that
actually occurs. These tests therefore also cannot run against SQLite.

They run at the repository layer rather than through the API because the
constraint is a schema property, and it must hold no matter which entry
point reaches it — REST today, MCP tool call and offline-sync push in Phases
8-9 (Architecture §9). Pinning it here means it stays proven even if a router
is rewritten.
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

from app.core.database import AsyncSessionLocal
from app.models.contract import Contract
from app.models.employee import Employee
from app.models.enums import ContractStatus
from app.repositories.hr import ContractRepository, EmployeeRepository

#: Postgres SQLSTATE for exclusion_violation — what this constraint raises.
EXCLUSION_VIOLATION = "23P01"

CONSTRAINT_NAME = "contracts_active_period_overlap_excl"


@pytest_asyncio.fixture
async def employee():
    """A committed Employee, torn down with its contracts afterwards.

    Each test gets its own, so one test's contracts can never collide with
    another's (or with a previous run's leftovers) through the very constraint
    under test. Committed for real, because an EXCLUDE constraint only fires on
    a real INSERT — a rollback-per-test fixture would defeat the whole point.
    """
    async with AsyncSessionLocal() as s:
        emp = await EmployeeRepository(s).create(Employee(
            public_id="temp",
            first_name="Overlap",
            last_name="Fixture",
            work_email=f"overlap.fixture.{uuid.uuid4().hex[:10]}@peoplepay360.com",
        ))
        await s.commit()
        employee_id = emp.id

    yield employee_id

    async with AsyncSessionLocal() as s:
        await s.execute(delete(Contract).where(Contract.employee_id == employee_id))
        await s.execute(delete(Employee).where(Employee.id == employee_id))
        await s.commit()


def _contract(employee_id: int, start: date, end: date | None, status: ContractStatus) -> Contract:
    return Contract(
        public_id="temp",
        employee_id=employee_id,
        wage=Decimal("50000.00"),
        start_date=start,
        end_date=end,
        status=status,
    )


async def _insert(contract: Contract) -> Contract:
    """Insert and COMMIT in its own session, so each insert is a separate
    transaction — which is what makes the constraint, not a flush ordering
    accident, the thing being tested.

    Goes through ContractRepository rather than `session.add` so the row gets a
    real minted `public_id`; the placeholder "temp" would collide on
    `ix_contracts_public_id` and raise a unique violation that looks
    superficially like the exclusion violation these tests are hunting.
    """
    async with AsyncSessionLocal() as s:
        created = await ContractRepository(s).create(contract)
        await s.commit()
        return created


async def _insert_expecting_exclusion(contract: Contract) -> IntegrityError:
    with pytest.raises(IntegrityError) as exc_info:
        await _insert(contract)
    return exc_info.value


def _sqlstate(exc: IntegrityError) -> str | None:
    return getattr(exc.orig, "sqlstate", None)


# ------------------------------------------------------------ the rejection

async def test_two_overlapping_active_contracts_are_rejected_by_postgres(employee):
    """The headline guarantee. Two active contracts whose date ranges overlap
    must be refused by the DATABASE with SQLSTATE 23P01."""
    await _insert(_contract(employee, date(2026, 1, 1), date(2026, 6, 30), ContractStatus.ACTIVE))

    exc = await _insert_expecting_exclusion(
        _contract(employee, date(2026, 4, 1), date(2026, 12, 31), ContractStatus.ACTIVE)
    )

    assert _sqlstate(exc) == EXCLUSION_VIOLATION
    assert CONSTRAINT_NAME in str(exc.orig)


async def test_contracts_touching_on_a_single_shared_day_overlap(employee):
    """`daterange(..., '[]')` is inclusive at BOTH ends, so a contract ending
    on the 30th and one starting on the 30th conflict — both are in force that
    day, and payroll would have two candidates for it.

    This is the boundary an exclusive-upper `'[)'` range would silently allow,
    which is exactly why Architecture §6 spells the bounds out.
    """
    await _insert(_contract(employee, date(2026, 1, 1), date(2026, 6, 30), ContractStatus.ACTIVE))

    exc = await _insert_expecting_exclusion(
        _contract(employee, date(2026, 6, 30), date(2026, 12, 31), ContractStatus.ACTIVE)
    )
    assert _sqlstate(exc) == EXCLUSION_VIOLATION


async def test_an_open_ended_active_contract_blocks_everything_after_its_start(employee):
    """`end_date IS NULL` is an unbounded daterange, so an open-ended contract
    conflicts with any later active one."""
    await _insert(_contract(employee, date(2026, 1, 1), None, ContractStatus.ACTIVE))

    exc = await _insert_expecting_exclusion(
        _contract(employee, date(2027, 5, 1), date(2027, 12, 31), ContractStatus.ACTIVE)
    )
    assert _sqlstate(exc) == EXCLUSION_VIOLATION


# -------------------------------------------------------- what IS allowed

async def test_a_cancelled_contract_may_overlap_an_active_one(employee):
    """`WHERE (status = 'active')` means only active rows participate.

    A cancelled contract covering the same dates is not a conflict — it is a
    decision that was reversed, and history has to remain representable. This
    is the case the phase brief calls out specifically.
    """
    await _insert(_contract(employee, date(2026, 1, 1), date(2026, 12, 31), ContractStatus.CANCELLED))

    # Fully overlapping, and must be accepted.
    await _insert(_contract(employee, date(2026, 1, 1), date(2026, 12, 31), ContractStatus.ACTIVE))

    async with AsyncSessionLocal() as s:
        rows = (await s.execute(text("SELECT status FROM contracts WHERE employee_id = :e ORDER BY status"),
                                {"e": employee})).scalars().all()
    assert sorted(rows) == ["active", "cancelled"]


async def test_draft_and_expired_contracts_may_overlap_freely(employee):
    """Proposals and history are not conflicts either — only `active` is."""
    await _insert(_contract(employee, date(2026, 1, 1), date(2026, 12, 31), ContractStatus.DRAFT))
    await _insert(_contract(employee, date(2026, 1, 1), date(2026, 12, 31), ContractStatus.DRAFT))
    await _insert(_contract(employee, date(2026, 1, 1), date(2026, 12, 31), ContractStatus.EXPIRED))

    async with AsyncSessionLocal() as s:
        count = (await s.execute(
            text("SELECT count(*) FROM contracts WHERE employee_id = :e"), {"e": employee}
        )).scalar()
    assert count == 3


async def test_consecutive_non_overlapping_active_contracts_are_allowed(employee):
    """The normal case: a renewal starting the day after the previous one
    ended. If this failed, nobody could ever be given a second contract."""
    await _insert(_contract(employee, date(2026, 1, 1), date(2026, 6, 30), ContractStatus.ACTIVE))
    await _insert(_contract(employee, date(2026, 7, 1), date(2026, 12, 31), ContractStatus.ACTIVE))

    async with AsyncSessionLocal() as s:
        count = (await s.execute(
            text("SELECT count(*) FROM contracts WHERE employee_id = :e AND status = 'active'"), {"e": employee}
        )).scalar()
    assert count == 2


async def test_the_constraint_is_scoped_per_employee(employee):
    """`employee_id WITH =` — two different people may hold active contracts
    over identical dates. Without that clause the whole company could hold one
    contract between them."""
    async with AsyncSessionLocal() as s:
        other = await EmployeeRepository(s).create(Employee(
            public_id="temp",
            first_name="Second",
            last_name="Person",
            work_email=f"second.person.{uuid.uuid4().hex[:10]}@peoplepay360.com",
        ))
        await s.commit()
        other_id = other.id

    try:
        await _insert(_contract(employee, date(2026, 1, 1), date(2026, 12, 31), ContractStatus.ACTIVE))
        await _insert(_contract(other_id, date(2026, 1, 1), date(2026, 12, 31), ContractStatus.ACTIVE))
    finally:
        async with AsyncSessionLocal() as s:
            await s.execute(delete(Contract).where(Contract.employee_id == other_id))
            await s.execute(delete(Employee).where(Employee.id == other_id))
            await s.commit()


# ------------------------------------------- the trap BaseService warns about

async def test_activating_a_cancelled_contract_into_a_taken_period_is_rejected(employee):
    """A status-only UPDATE moves a row INTO the constrained set.

    This is the exact trap BaseService's docstring exists to prevent: the
    call site looks harmless ("just change a status"), but under a predicated
    EXCLUDE constraint it is precisely what triggers a violation. Any service
    method that can set status='active' owes the 23P01 -> 409 translation, not
    only the create path.
    """
    await _insert(_contract(employee, date(2026, 1, 1), date(2026, 12, 31), ContractStatus.ACTIVE))

    cancelled = _contract(employee, date(2026, 3, 1), date(2026, 9, 30), ContractStatus.CANCELLED)
    await _insert(cancelled)

    with pytest.raises(IntegrityError) as exc_info:
        async with AsyncSessionLocal() as s:
            await s.execute(
                text("UPDATE contracts SET status = 'active' WHERE id = :cid"), {"cid": cancelled.id}
            )
            await s.commit()

    assert _sqlstate(exc_info.value) == EXCLUSION_VIOLATION


# ------------------------------------------------------ the definition itself

async def test_the_constraint_definition_matches_architecture_section_6():
    """Pin the predicate itself.

    The predicate compares `status` against the STRING 'active'. If someone
    ever changes ContractStatus to store 'ACTIVE', this constraint stops
    matching any row and silently protects nothing — no error, no failing
    insert, just a guarantee that quietly evaporates. Asserting on the stored
    definition is the only way that shows up as a test failure.
    """
    async with AsyncSessionLocal() as s:
        definition = (await s.execute(
            text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n"),
            {"n": CONSTRAINT_NAME},
        )).scalar()

    assert definition is not None, f"{CONSTRAINT_NAME} does not exist"
    normalised = " ".join(definition.split()).lower()

    assert "exclude using gist" in normalised
    assert "employee_id with =" in normalised
    assert "daterange(start_date, end_date, '[]'" in normalised
    assert "with &&" in normalised
    assert "'active'" in normalised
    assert "status" in normalised
