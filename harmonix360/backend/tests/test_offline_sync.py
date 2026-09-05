"""Phase 8 — offline sync for attendance and time-off requests (PS §5.3).

The property PRD §7 actually measures is "zero duplicate records across a
kill-network → mutate → reconnect cycle", so `test_retried_mutation_id_creates_exactly_one_row`
is the centre of this module: it counts rows in PostgreSQL, not results in a
response body.

The rest of it exists because registering an entity for sync is where a second,
looser path to the database would appear if one ever did (Architecture §9).
Each test below is one way that could happen:

  - an employee writing somebody else's attendance          (authorization)
  - an employee reading somebody else's attendance          (row scoping)
  - a correction arriving as an UPDATE instead of a PATCH   (operation scope)
  - the client dictating worked_hours or status             (derivation)
  - a payroll entity becoming syncable                      (registry contents,
                                                             in test_platform_layer)
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models.attendance import Attendance
from app.models.employee import Employee
from app.models.enums import TimeOffUnit, UserRole
from app.models.time_off import TimeOffRequest, TimeOffType
from app.repositories.hr import EmployeeRepository, TimeOffTypeRepository

from tests.conftest import auth_headers, unique_email

BASE = "/api/v1"


def mutation(entity_type: str, payload: dict, *, op: str = "CREATE", **extra) -> dict:
    return {
        "client_mutation_id": f"cm_{uuid.uuid4().hex[:16]}",
        "entity_type": entity_type,
        "op": op,
        "payload": payload,
        **extra,
    }


async def push(client, headers, *mutations) -> list[dict]:
    response = await client.post(
        f"{BASE}/sync/push", headers=headers, json={"mutations": list(mutations)}
    )
    assert response.status_code == 200, response.text
    return response.json()["results"]


@pytest_asyncio.fixture
async def two_employees(department):
    """Two committed employees in one department, torn down afterwards.

    Committed rather than rolled back for the same reason `conftest`'s
    `department` fixture is: the ASGI client's request handler opens its own
    session and cannot see an uncommitted row.
    """
    async with AsyncSessionLocal() as session:
        repo = EmployeeRepository(session)
        mine = await repo.create(
            Employee(
                public_id="temp",
                first_name="Sync",
                last_name="Mine",
                work_email=unique_email("sync.mine"),
            )
        )
        theirs = await repo.create(
            Employee(
                public_id="temp",
                first_name="Sync",
                last_name="Theirs",
                work_email=unique_email("sync.theirs"),
            )
        )
        await session.commit()
        ids = (mine.id, mine.public_id, theirs.id, theirs.public_id)

    yield {"mine_id": ids[0], "mine": ids[1], "theirs_id": ids[2], "theirs": ids[3]}

    async with AsyncSessionLocal() as session:
        await session.execute(
            Attendance.__table__.delete().where(
                Attendance.employee_id.in_([ids[0], ids[2]])
            )
        )
        await session.execute(
            TimeOffRequest.__table__.delete().where(
                TimeOffRequest.employee_id.in_([ids[0], ids[2]])
            )
        )
        await session.execute(
            Employee.__table__.delete().where(Employee.id.in_([ids[0], ids[2]]))
        )
        await session.commit()


@pytest_asyncio.fixture
async def leave_type():
    async with AsyncSessionLocal() as session:
        row = await TimeOffTypeRepository(session).create(
            TimeOffType(
                public_id="temp",
                name="Sync Test Leave",
                code=f"SYNC_{uuid.uuid4().hex[:8].upper()}",
                unit=TimeOffUnit.DAYS,
                requires_allocation=False,
                requires_approval=True,
                payroll_integration=False,
            )
        )
        await session.commit()
        type_id, public_id = row.id, row.public_id

    yield public_id

    async with AsyncSessionLocal() as session:
        # Requests first. pytest tears fixtures down in reverse setup order, so
        # this runs BEFORE `two_employees` clears its rows — and a type with a
        # request still pointing at it cannot be deleted (which is Phase 2's
        # rule working, not a fixture problem).
        await session.execute(
            TimeOffRequest.__table__.delete().where(
                TimeOffRequest.time_off_type_id == type_id
            )
        )
        await session.execute(
            TimeOffType.__table__.delete().where(TimeOffType.id == type_id)
        )
        await session.commit()


async def attendance_count(employee_id: int) -> int:
    async with AsyncSessionLocal() as session:
        return (
            await session.execute(
                select(func.count(Attendance.id)).where(
                    Attendance.employee_id == employee_id
                )
            )
        ).scalar_one()


# ------------------------------------------------------------- idempotency


async def test_retried_mutation_id_creates_exactly_one_row(client, two_employees):
    """PRD §7: "zero duplicate records across a kill-network → mutate →
    reconnect cycle".

    The realistic failure is not a client sending twice on purpose. It is a
    client that sent successfully, never saw the response because the network
    dropped, and retried the same queued mutation on reconnect — which is why
    `client_mutation_id` is generated when the mutation is QUEUED, not when it
    is sent. The same id arriving twice must produce one row.

    Counted in PostgreSQL, not inferred from the response.
    """
    headers = auth_headers(UserRole.EMPLOYEE, employee_id=two_employees["mine"])
    check_in = datetime.now(UTC).replace(microsecond=0)
    payload = {
        "employee_id": two_employees["mine"],
        "check_in": check_in.isoformat(),
        "check_out": (check_in + timedelta(hours=7)).isoformat(),
    }
    once = mutation("attendance", payload)

    before = await attendance_count(two_employees["mine_id"])

    first = (await push(client, headers, once))[0]
    assert first["outcome"] == "applied", first
    created_id = first["entity_id"]

    # The identical mutation again — same client_mutation_id, same payload.
    second = (await push(client, headers, dict(once)))[0]

    assert second["outcome"] == "applied"
    # Not merely "did not create a second row": the replay returns the FIRST
    # result verbatim, so the client reconciles against the id it would have
    # got the first time rather than being told its row does not exist.
    assert second["entity_id"] == created_id
    assert second == first

    assert await attendance_count(two_employees["mine_id"]) == before + 1

    # A third time, in the same batch as an unrelated new mutation, because a
    # reconnect flushes the whole outbox rather than one entry.
    other = mutation(
        "attendance",
        {
            "employee_id": two_employees["mine"],
            "check_in": (check_in + timedelta(days=1)).isoformat(),
            "check_out": (check_in + timedelta(days=1, hours=7)).isoformat(),
        },
    )
    results = await push(client, headers, dict(once), other)
    assert [r["outcome"] for r in results] == ["applied", "applied"]
    assert results[0]["entity_id"] == created_id
    assert await attendance_count(two_employees["mine_id"]) == before + 2


async def test_idempotency_is_scoped_to_the_actor(client, two_employees):
    """The replay key is (actor_key, client_mutation_id).

    Two devices belonging to two different people can independently generate
    the same id — unlikely with UUIDs, but the key is what makes it harmless,
    and a key that ignored the actor would let one person's retry silently
    return another person's result.
    """
    shared_id = f"cm_{uuid.uuid4().hex[:16]}"
    check_in = datetime.now(UTC).replace(microsecond=0)

    mine_headers = auth_headers(UserRole.EMPLOYEE, employee_id=two_employees["mine"])
    hr_headers = auth_headers(UserRole.HR_MANAGER)

    mine = await push(
        client,
        mine_headers,
        {
            "client_mutation_id": shared_id,
            "entity_type": "attendance",
            "op": "CREATE",
            "payload": {
                "employee_id": two_employees["mine"],
                "check_in": check_in.isoformat(),
                "check_out": (check_in + timedelta(hours=7)).isoformat(),
            },
        },
    )
    theirs = await push(
        client,
        hr_headers,
        {
            "client_mutation_id": shared_id,
            "entity_type": "attendance",
            "op": "CREATE",
            "payload": {
                "employee_id": two_employees["theirs"],
                "check_in": check_in.isoformat(),
                "check_out": (check_in + timedelta(hours=7)).isoformat(),
            },
        },
    )

    assert mine[0]["outcome"] == "applied"
    assert theirs[0]["outcome"] == "applied"
    assert mine[0]["entity_id"] != theirs[0]["entity_id"]


# ----------------------------------------------------------- authorization


async def test_employee_cannot_sync_attendance_for_someone_else(
    client, two_employees
):
    """The check that would have been skipped by the generic create path.

    `AttendanceService.create_attendance` calls `employee_for`, which compares
    against the SIGNED `employee_id` claim — not the `employee_id` in the
    payload. Sending someone else's id is refused, and refused as a per-mutation
    `rejected` outcome rather than a 500 that would abort the whole batch.
    """
    headers = auth_headers(UserRole.EMPLOYEE, employee_id=two_employees["mine"])
    check_in = datetime.now(UTC).replace(microsecond=0)

    before = await attendance_count(two_employees["theirs_id"])
    results = await push(
        client,
        headers,
        mutation(
            "attendance",
            {
                "employee_id": two_employees["theirs"],
                "check_in": check_in.isoformat(),
                "check_out": (check_in + timedelta(hours=7)).isoformat(),
            },
        ),
    )

    assert results[0]["outcome"] == "rejected"
    assert results[0]["error"]["code"] == "FORBIDDEN"
    assert await attendance_count(two_employees["theirs_id"]) == before


async def test_one_rejected_mutation_does_not_abort_the_batch(client, two_employees):
    """A reconnect flushes everything queued at once. If a single bad entry
    could 500 the request, one unsyncable row would strand every other row on
    the device indefinitely."""
    headers = auth_headers(UserRole.EMPLOYEE, employee_id=two_employees["mine"])
    check_in = datetime.now(UTC).replace(microsecond=0)

    before = await attendance_count(two_employees["mine_id"])
    results = await push(
        client,
        headers,
        mutation(
            "attendance",
            {
                "employee_id": two_employees["theirs"],  # not mine — refused
                "check_in": check_in.isoformat(),
            },
        ),
        mutation(
            "attendance",
            {
                "employee_id": two_employees["mine"],  # mine — applied
                "check_in": (check_in + timedelta(hours=2)).isoformat(),
                "check_out": (check_in + timedelta(hours=9)).isoformat(),
            },
        ),
    )

    assert [r["outcome"] for r in results] == ["rejected", "applied"]
    assert await attendance_count(two_employees["mine_id"]) == before + 1


# --------------------------------------------------------- operation scope


@pytest.mark.parametrize("op", ["UPDATE", "DELETE"])
async def test_corrections_are_not_syncable(client, two_employees, op):
    """Architecture §8.3: check-in/check-out creation syncs; CORRECTIONS stay
    online-only and role-gated.

    A correction is an UPDATE. `AttendanceService.correct` guards it with
    `require_hr`; the engine's generic UPDATE path does not. Without
    `allowed_ops` the correction gate would be bypassed simply by choosing a
    different verb — so this asserts the row is untouched, not just that the
    response says "rejected".
    """
    employee_headers = auth_headers(
        UserRole.EMPLOYEE, employee_id=two_employees["mine"]
    )
    check_in = datetime.now(UTC).replace(microsecond=0)
    created = (
        await push(
            client,
            employee_headers,
            mutation(
                "attendance",
                {
                    "employee_id": two_employees["mine"],
                    "check_in": check_in.isoformat(),
                    "check_out": (check_in + timedelta(hours=7)).isoformat(),
                },
            ),
        )
    )[0]
    assert created["outcome"] == "applied"

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(Attendance).where(Attendance.public_id == created["entity_id"])
            )
        ).scalar_one()
        before = (row.check_in, row.check_out, row.worked_hours, row.deleted_at)

    # Even as HR, who *may* correct — through PATCH /attendance/{id}, not here.
    results = await push(
        client,
        auth_headers(UserRole.HR_MANAGER),
        mutation(
            "attendance",
            {
                "employee_id": two_employees["mine"],
                "check_in": (check_in - timedelta(hours=3)).isoformat(),
                "check_out": (check_in + timedelta(hours=7)).isoformat(),
            },
            op=op,
            entity_id=created["entity_id"],
            known_version=created["version"],
        ),
    )

    assert results[0]["outcome"] == "rejected"
    assert results[0]["error"]["code"] == "OPERATION_NOT_SYNCABLE"

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(Attendance).where(Attendance.public_id == created["entity_id"])
            )
        ).scalar_one()
        assert (row.check_in, row.check_out, row.worked_hours, row.deleted_at) == before


async def test_time_off_approval_is_not_reachable_through_sync(
    client, two_employees, leave_type
):
    """Approval debits a live allocation inside a transaction. A device holding
    a stale copy of that balance must not get to make the decision, so the only
    syncable time-off operation is submission."""
    headers = auth_headers(UserRole.EMPLOYEE, employee_id=two_employees["mine"])
    created = (
        await push(
            client,
            headers,
            mutation(
                "time_off_request",
                {
                    "employee_id": two_employees["mine"],
                    "time_off_type_id": leave_type,
                    "date_from": "2026-11-02",
                    "date_to": "2026-11-04",
                },
            ),
        )
    )[0]
    assert created["outcome"] == "applied"

    results = await push(
        client,
        auth_headers(UserRole.HR_MANAGER),
        mutation(
            "time_off_request",
            {"status": "approved"},
            op="UPDATE",
            entity_id=created["entity_id"],
            known_version=created["version"],
        ),
    )
    assert results[0]["outcome"] == "rejected"
    assert results[0]["error"]["code"] == "OPERATION_NOT_SYNCABLE"

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(TimeOffRequest).where(
                    TimeOffRequest.public_id == created["entity_id"]
                )
            )
        ).scalar_one()
        assert row.status.value == "to_approve"


# ------------------------------------------------------------- derivation


async def test_server_derives_worked_hours_and_status_not_the_client(
    client, two_employees
):
    """`worked_hours` and `status` are server-owned (PS B3). A synced row must
    be computed by `AttendanceService._compute`, exactly as an online one is —
    otherwise an offline device could dictate its own overtime."""
    headers = auth_headers(UserRole.EMPLOYEE, employee_id=two_employees["mine"])
    check_in = datetime.now(UTC).replace(microsecond=0)
    results = await push(
        client,
        headers,
        mutation(
            "attendance",
            {
                "employee_id": two_employees["mine"],
                "check_in": check_in.isoformat(),
                "check_out": (check_in + timedelta(hours=7)).isoformat(),
                # Both ignored: `AttendanceCreate` forbids unknown fields, so
                # these are refused at the schema rather than silently dropped.
            },
        ),
    )
    assert results[0]["outcome"] == "applied"

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(Attendance).where(Attendance.public_id == results[0]["entity_id"])
            )
        ).scalar_one()
        assert row.worked_hours is not None
        assert float(row.worked_hours) == pytest.approx(7.0)
        assert row.status is not None


async def test_client_cannot_smuggle_server_owned_fields(client, two_employees):
    """`AttendanceCreate` is `extra="forbid"`, and the sync path builds that
    same schema — so a payload carrying `worked_hours` or `status` is rejected
    rather than partially honoured."""
    headers = auth_headers(UserRole.EMPLOYEE, employee_id=two_employees["mine"])
    check_in = datetime.now(UTC).replace(microsecond=0)
    results = await push(
        client,
        headers,
        mutation(
            "attendance",
            {
                "employee_id": two_employees["mine"],
                "check_in": check_in.isoformat(),
                "worked_hours": "99.00",
                "status": "overtime",
            },
        ),
    )
    assert results[0]["outcome"] == "rejected"
    assert results[0]["error"]["code"] == "VALIDATION_ERROR"


async def test_time_off_duration_is_computed_by_the_service(
    client, two_employees, leave_type
):
    """3 inclusive calendar days, computed by `request_duration` — the same
    function an online submission uses, because the number it produces is what
    a balance is later debited by."""
    headers = auth_headers(UserRole.EMPLOYEE, employee_id=two_employees["mine"])
    results = await push(
        client,
        headers,
        mutation(
            "time_off_request",
            {
                "employee_id": two_employees["mine"],
                "time_off_type_id": leave_type,
                "date_from": "2026-11-02",
                "date_to": "2026-11-04",
                "reason": "queued offline",
            },
        ),
    )
    assert results[0]["outcome"] == "applied"

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(TimeOffRequest).where(
                    TimeOffRequest.public_id == results[0]["entity_id"]
                )
            )
        ).scalar_one()
        assert float(row.duration) == 3.0
        assert row.status.value == "to_approve"


# ------------------------------------------------------------ row scoping


async def test_pull_gives_an_employee_only_their_own_rows(client, two_employees):
    """`SyncService.pull` filters by tenant and cursor only. Without the
    registration's `scope_filter`, every authenticated login pulling
    `attendance` would receive the whole organisation's attendance —
    Architecture §5's first row, silently inverted."""
    check_in = datetime.now(UTC).replace(microsecond=0)
    hr_headers = auth_headers(UserRole.HR_MANAGER)

    mine = (
        await push(
            client,
            auth_headers(UserRole.EMPLOYEE, employee_id=two_employees["mine"]),
            mutation(
                "attendance",
                {
                    "employee_id": two_employees["mine"],
                    "check_in": check_in.isoformat(),
                    "check_out": (check_in + timedelta(hours=7)).isoformat(),
                },
            ),
        )
    )[0]
    theirs = (
        await push(
            client,
            hr_headers,
            mutation(
                "attendance",
                {
                    "employee_id": two_employees["theirs"],
                    "check_in": check_in.isoformat(),
                    "check_out": (check_in + timedelta(hours=7)).isoformat(),
                },
            ),
        )
    )[0]
    assert mine["outcome"] == "applied" and theirs["outcome"] == "applied"

    # The safety lag means a row is not pulled until it is a couple of seconds
    # old; ask for everything and assert on membership rather than on counts.
    await asyncio.sleep(3)

    async def pulled(headers):
        response = await client.get(
            f"{BASE}/sync/pull",
            headers=headers,
            params={"entity_types": "attendance", "limit": 1000},
        )
        assert response.status_code == 200, response.text
        return {e["public_id"] for e in response.json()["entities"]}

    employee_view = await pulled(
        auth_headers(UserRole.EMPLOYEE, employee_id=two_employees["mine"])
    )
    assert mine["entity_id"] in employee_view
    assert theirs["entity_id"] not in employee_view

    hr_view = await pulled(hr_headers)
    assert {mine["entity_id"], theirs["entity_id"]} <= hr_view


async def test_pull_gives_a_login_with_no_employee_no_rows(client, two_employees):
    """An Employee-role token with no `employee_id` claim has no own-rows to
    scope to. It gets nothing, not everything — the direction a scoping bug
    fails in matters more than how likely the case is."""
    check_in = datetime.now(UTC).replace(microsecond=0)
    created = (
        await push(
            client,
            auth_headers(UserRole.HR_MANAGER),
            mutation(
                "attendance",
                {
                    "employee_id": two_employees["mine"],
                    "check_in": check_in.isoformat(),
                    "check_out": (check_in + timedelta(hours=7)).isoformat(),
                },
            ),
        )
    )[0]
    assert created["outcome"] == "applied"

    await asyncio.sleep(3)

    response = await client.get(
        f"{BASE}/sync/pull",
        headers=auth_headers(UserRole.EMPLOYEE),  # no employee_id claim
        params={"entity_types": "attendance", "limit": 1000},
    )
    assert response.status_code == 200, response.text
    assert response.json()["entities"] == []


# --------------------------------------------------------- registry limits


async def test_payroll_is_not_syncable(client):
    """The registry is the whole opt-in mechanism, so the refusal for a payroll
    entity is a 400 from the router naming what IS registered."""
    response = await client.get(
        f"{BASE}/sync/pull",
        headers=auth_headers(UserRole.ADMIN),
        params={"entity_types": "payslip"},
    )
    assert response.status_code == 400
    assert "Unknown entity_types" in response.json()["detail"]

    results = await push(
        client,
        auth_headers(UserRole.ADMIN),
        mutation("payrun", {"name": "should never apply"}),
    )
    assert results[0]["outcome"] == "rejected"
    assert results[0]["error"]["code"] == "UNKNOWN_ENTITY_TYPE"
