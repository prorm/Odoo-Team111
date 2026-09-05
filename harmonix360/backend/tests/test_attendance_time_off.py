"""Phase 2 acceptance against real PostgreSQL, including synchronized races."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.api.v1.deps import CurrentUser
from app.core.database import AsyncSessionLocal
from app.core.security import create_access_token
from app.models.enums import UserRole
from app.models.time_off import TimeOffAllocation, TimeOffRequest, TimeOffType
from app.schemas.time_off import Decision
from app.services.attendance import (
    AttendanceService,
    derive_attendance_status,
    worked_hours,
)
from app.services.time_off import TimeOffRequestService
from tests.test_employee_api import _create


def auth_headers(role, *, employee_id=None):
    emails = {
        UserRole.HR_MANAGER: "hr.manager",
        UserRole.HR_PAYROLL_USER: "payroll.user",
        UserRole.HR_PAYROLL_MANAGER: "payroll.manager",
    }
    token = create_access_token(
        subject=f"{emails.get(role, role.value)}@peoplepay360.com",
        role=role.value,
        employee_id=employee_id,
    )
    return {"Authorization": f"Bearer {token}"}


HR = auth_headers(UserRole.HR_MANAGER)
BASE = "/api/v1"
DAY = "2026-09-07"


@pytest_asyncio.fixture
async def leave_setup(client, cleanup_employees):
    employee = await _create(client)
    other = await _create(client)
    types = []

    async def make_type(**kwargs):
        body = {"name": "Annual leave", "code": uuid.uuid4().hex, **kwargs}
        res = await client.post(f"{BASE}/time-off-types/", headers=HR, json=body)
        assert res.status_code == 201, res.text
        types.append(res.json()["id"])
        return res.json()

    leave_type = await make_type()
    yield employee, other, leave_type, make_type
    async with AsyncSessionLocal() as s:
        ids = (
            (
                await s.execute(
                    select(TimeOffType.id).where(TimeOffType.public_id.in_(types))
                )
            )
            .scalars()
            .all()
        )
        await s.execute(
            delete(TimeOffRequest).where(TimeOffRequest.time_off_type_id.in_(ids))
        )
        await s.execute(
            delete(TimeOffAllocation).where(TimeOffAllocation.time_off_type_id.in_(ids))
        )
        await s.execute(delete(TimeOffType).where(TimeOffType.id.in_(ids)))
        await s.commit()


async def allocation(client, employee, leave_type, amount="2", **kwargs):
    res = await client.post(
        f"{BASE}/time-off-allocations/",
        headers=HR,
        json={
            "employee_id": employee["id"],
            "time_off_type_id": leave_type["id"],
            "allocated": amount,
            "valid_from": "2026-01-01",
            "valid_to": "2026-12-31",
            **kwargs,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


async def request(client, employee, leave_type, **kwargs):
    res = await client.post(
        f"{BASE}/time-off-requests/",
        headers=auth_headers(UserRole.EMPLOYEE, employee_id=employee["id"]),
        json={
            "employee_id": employee["id"],
            "time_off_type_id": leave_type["id"],
            "date_from": DAY,
            "date_to": DAY,
            "reason": "Family time",
            **kwargs,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


async def balance(client, row):
    res = await client.get(f"{BASE}/time-off-allocations/{row['id']}", headers=HR)
    assert res.status_code == 200, res.text
    return res.json()


async def decide(client, row, action="approve", role=UserRole.HR_MANAGER):
    return await client.post(
        f"{BASE}/time-off-requests/{row['id']}/{action}",
        headers=auth_headers(role),
        json={"version": row["version"], "decision_note": "Reviewed"},
    )


async def test_full_leave_happy_path_and_no_pending_reservation(client, leave_setup):
    emp, _, kind, _ = leave_setup
    alloc = await allocation(client, emp, kind)
    req = await request(client, emp, kind)
    assert req["status"] == "to_approve"
    assert Decimal(req["duration"]) == 1
    assert (await balance(client, alloc))["remaining"] == "2.00"
    approved = await decide(client, req)
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "approved"
    assert body["allocation_id"] == alloc["id"]
    assert body["approved_by"].startswith("usr_")
    assert body["employee"]["id"] == emp["id"]
    updated = await balance(client, alloc)
    assert Decimal(updated["taken"]) == 1
    assert Decimal(updated["remaining"]) == 1
    assert updated["version"] > alloc["version"]
    assert (await decide(client, req)).status_code == 409
    assert (await balance(client, alloc))["remaining"] == "1.00"


async def test_refuse_leaves_balance_and_version_untouched(client, leave_setup):
    emp, _, kind, _ = leave_setup
    alloc = await allocation(client, emp, kind)
    req = await request(client, emp, kind)
    res = await decide(client, req, "refuse")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "refused"
    assert res.json()["allocation_id"] is None
    assert await balance(client, alloc) == alloc


@pytest.mark.parametrize(
    "case",
    ["insufficient", "missing", "expired", "draft", "wrong_employee", "wrong_type"],
)
async def test_approval_failure_is_atomic(client, leave_setup, case):
    emp, other, kind, make_type = leave_setup
    alloc = None
    if case != "missing":
        alloc = await allocation(
            client,
            other if case == "wrong_employee" else emp,
            await make_type() if case == "wrong_type" else kind,
            amount="0.50" if case == "insufficient" else "2",
            **(
                {"valid_to": "2026-08-31"}
                if case == "expired"
                else {"status": "draft"} if case == "draft" else {}
            ),
        )
    req = await request(client, emp, kind)
    res = await decide(client, req)
    assert res.status_code == 409, res.text
    assert "balance" in res.text if case == "insufficient" else "allocation" in res.text
    current = await client.get(f"{BASE}/time-off-requests/{req['id']}", headers=HR)
    assert current.json()["status"] == "to_approve"
    assert current.json()["version"] == req["version"]
    if alloc:
        assert await balance(client, alloc) == alloc


@pytest.mark.parametrize("automatic", [False, True])
async def test_unallocated_type_never_looks_up_allocation(
    client, leave_setup, monkeypatch, automatic
):
    emp, _, _, make_type = leave_setup
    kind = await make_type(requires_allocation=False, requires_approval=not automatic)

    async def forbidden(*args):
        raise AssertionError("Allocation lookup is forbidden for this type")

    monkeypatch.setattr(TimeOffRequestService, "_matching_allocation", forbidden)
    req = await request(client, emp, kind)
    if not automatic:
        res = await decide(client, req)
        assert res.status_code == 200, res.text
        req = res.json()
    assert req["status"] == "approved"
    assert req["allocation_id"] is None


async def test_auto_approval_debits_and_failed_submission_leaves_no_request(
    client, leave_setup
):
    emp, _, _, make_type = leave_setup
    kind = await make_type(requires_approval=False)
    alloc = await allocation(client, emp, kind, "1")
    assert (await request(client, emp, kind))["status"] == "approved"
    assert (await balance(client, alloc))["remaining"] == "0.00"
    res = await client.post(
        f"{BASE}/time-off-requests/",
        headers=HR,
        json={
            "employee_id": emp["id"],
            "time_off_type_id": kind["id"],
            "date_from": DAY,
            "date_to": DAY,
        },
    )
    assert res.status_code == 409
    rows = await client.get(
        f"{BASE}/time-off-requests/?employee_id={emp['id']}", headers=HR
    )
    assert rows.json()["total"] == 1


@pytest.mark.parametrize("role", list(UserRole))
@pytest.mark.parametrize("action", ["approve", "refuse"])
async def test_leave_decision_rbac_every_role(client, leave_setup, role, action):
    emp, _, kind, _ = leave_setup
    alloc = await allocation(client, emp, kind)
    req = await request(client, emp, kind)
    res = await decide(client, req, action, role)
    assert res.status_code == (403 if role == UserRole.EMPLOYEE else 200), res.text
    if role == UserRole.EMPLOYEE:
        assert await balance(client, alloc) == alloc


async def test_direct_service_denies_employee_without_database():
    user = CurrentUser("employee@example.com", UserRole.EMPLOYEE)
    with pytest.raises(HTTPException) as error:
        await AttendanceService(None).correct("att_unknown", None, user)
    assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        await TimeOffRequestService(None).decide(
            "req_unknown", None, user, approve=True
        )
    assert error.value.status_code == 403


async def test_two_concurrent_approvals_exactly_one_wins(
    client, leave_setup, monkeypatch
):
    emp, _, kind, _ = leave_setup
    alloc = await allocation(client, emp, kind, "1")
    requests = [await request(client, emp, kind) for _ in range(2)]
    barrier = asyncio.Barrier(2)
    original = TimeOffRequestService._matching_allocation
    loaded_versions = []

    async def synchronized(self, row):
        allocation_row = await original(self, row)
        loaded_versions.append(allocation_row.version)
        await asyncio.wait_for(barrier.wait(), 10)
        return allocation_row

    monkeypatch.setattr(TimeOffRequestService, "_matching_allocation", synchronized)

    async def approve(row):
        async with AsyncSessionLocal() as session:
            try:
                result = await TimeOffRequestService(session).decide(
                    row["id"],
                    Decision(version=row["version"]),
                    CurrentUser("hr.manager@peoplepay360.com", UserRole.HR_MANAGER),
                    approve=True,
                )
                await session.commit()
                return result.status.value
            except HTTPException as exc:
                await session.rollback()
                return exc.status_code

    results = await asyncio.wait_for(
        asyncio.gather(*(approve(row) for row in requests)), 20
    )
    assert sorted(map(str, results)) == ["409", "approved"]
    assert loaded_versions == [alloc["version"], alloc["version"]]
    current = await balance(client, alloc)
    assert current["taken"] == "1.00" and current["remaining"] == "0.00"
    assert current["version"] == alloc["version"] + 1
    rows = [
        (await client.get(f"{BASE}/time-off-requests/{row['id']}", headers=HR)).json()
        for row in requests
    ]
    assert sorted(row["status"] for row in rows) == ["approved", "to_approve"]


async def test_own_scope_and_computed_fields_rejected(client, leave_setup):
    emp, other, kind, _ = leave_setup
    alloc = await allocation(client, emp, kind)
    req = await request(client, emp, kind)
    mine = auth_headers(UserRole.EMPLOYEE, employee_id=emp["id"])
    theirs = auth_headers(UserRole.EMPLOYEE, employee_id=other["id"])
    for resource, row in [("time-off-allocations", alloc), ("time-off-requests", req)]:
        assert (
            await client.get(f"{BASE}/{resource}/", headers=mine)
        ).status_code == 403
        assert (await client.get(f"{BASE}/{resource}/me", headers=mine)).json()[
            "total"
        ] == 1
        assert (
            await client.get(f"{BASE}/{resource}/{row['id']}", headers=theirs)
        ).status_code == 403
    payload = {
        "employee_id": other["id"],
        "time_off_type_id": kind["id"],
        "date_from": DAY,
        "date_to": DAY,
    }
    assert (
        await client.post(f"{BASE}/time-off-requests/", headers=mine, json=payload)
    ).status_code == 403
    for field, value in [
        ("duration", 0),
        ("status", "approved"),
        ("approved_by", "usr_x"),
    ]:
        assert (
            await client.post(
                f"{BASE}/time-off-requests/", headers=HR, json={**payload, field: value}
            )
        ).status_code == 422


@pytest.mark.parametrize("role", list(UserRole))
async def test_attendance_correction_rbac(client, cleanup_employees, role):
    emp = await _create(client)
    res = await client.post(
        f"{BASE}/attendance/",
        headers=auth_headers(UserRole.EMPLOYEE, employee_id=emp["id"]),
        json={
            "employee_id": emp["id"],
            "check_in": f"{DAY}T09:00:00Z",
            "check_out": f"{DAY}T17:00:00Z",
        },
    )
    assert res.status_code == 201, res.text
    row = res.json()
    res = await client.patch(
        f"{BASE}/attendance/{row['id']}",
        headers=auth_headers(role),
        json={
            "version": row["version"],
            "check_in": f"{DAY}T09:00:00Z",
            "check_out": f"{DAY}T18:00:00Z",
            "correction_reason": "Forgot last hour",
        },
    )
    assert res.status_code == (403 if role == UserRole.EMPLOYEE else 200), res.text
    if role != UserRole.EMPLOYEE:
        assert res.json()["worked_hours"] == "9.00"
        assert res.json()["corrected_by"].startswith("usr_")


async def test_attendance_checkin_checkout_read_delete_and_scope(
    client, cleanup_employees
):
    emp = await _create(client)
    other = await _create(client)
    mine = auth_headers(UserRole.EMPLOYEE, employee_id=emp["id"])
    theirs = auth_headers(UserRole.EMPLOYEE, employee_id=other["id"])
    res = await client.post(
        f"{BASE}/attendance/check-in", headers=mine, json={"employee_id": emp["id"]}
    )
    assert res.status_code == 201, res.text
    row = res.json()
    assert row["worked_hours"] is None
    assert (await client.get(f"{BASE}/attendance/", headers=mine)).status_code == 403
    assert (await client.get(f"{BASE}/attendance/me", headers=mine)).json()[
        "total"
    ] == 1
    assert (
        await client.get(f"{BASE}/attendance/{row['id']}", headers=theirs)
    ).status_code == 403
    assert (
        await client.post(
            f"{BASE}/attendance/{row['id']}/check-out",
            headers=theirs,
            json={"version": row["version"]},
        )
    ).status_code == 403
    res = await client.post(
        f"{BASE}/attendance/{row['id']}/check-out",
        headers=mine,
        json={"version": row["version"]},
    )
    assert res.status_code == 200, res.text
    closed = res.json()
    assert closed["check_out"] and Decimal(closed["worked_hours"]) >= 0
    assert (
        await client.post(
            f"{BASE}/attendance/{row['id']}/check-out",
            headers=mine,
            json={"version": closed["version"]},
        )
    ).status_code == 409
    assert (
        await client.delete(
            f"{BASE}/attendance/{row['id']}?version={closed['version']}", headers=mine
        )
    ).status_code == 403
    assert (
        await client.delete(
            f"{BASE}/attendance/{row['id']}?version={closed['version']}", headers=HR
        )
    ).status_code == 200
    assert (
        await client.get(f"{BASE}/attendance/{row['id']}", headers=HR)
    ).status_code == 404


@pytest.mark.parametrize(
    "extra", [{"worked_hours": 8}, {"status": "present"}, {"corrected_by": "usr_x"}]
)
async def test_attendance_derived_fields_forbidden(client, cleanup_employees, extra):
    emp = await _create(client)
    res = await client.post(
        f"{BASE}/attendance/",
        headers=HR,
        json={"employee_id": emp["id"], "check_in": f"{DAY}T09:00:00Z", **extra},
    )
    assert res.status_code == 422


@pytest.mark.parametrize(
    "start,end,now,status",
    [
        (None, None, "2026-09-07T18:00", "absent"),
        ("2026-09-07T09:00", "2026-09-07T17:00", "2026-09-07T18:00", "present"),
        ("2026-09-07T09:01", "2026-09-07T17:00", "2026-09-07T18:00", "late"),
        ("2026-09-07T09:00", "2026-09-07T18:00", "2026-09-07T18:00", "overtime"),
        ("2026-09-07T09:00", None, "2026-09-08T00:00", "missing_checkout"),
        ("2026-09-07T09:00", None, "2026-09-07T23:59", "present"),
        ("2026-09-07T09:00", "2026-09-07T09:00", "2026-09-07T18:00", "absent"),
    ],
)
def test_pure_status(start, end, now, status):
    def dt(value):
        return datetime.fromisoformat(value).replace(tzinfo=UTC) if value else None

    result = derive_attendance_status(
        dt(start),
        dt(end),
        now=dt(now),
        expected_start=dt("2026-09-07T09:00"),
        expected_hours=Decimal(8),
    )
    assert result.value == status


def test_worked_hours_rounding_and_invalid_order():
    start = datetime(2026, 9, 7, 9, tzinfo=UTC)
    assert worked_hours(start, start + timedelta(minutes=90)) == Decimal("1.50")
    with pytest.raises(ValueError):
        worked_hours(start, start - timedelta(seconds=1))


async def test_type_and_allocation_crud_and_permissions(client, leave_setup):
    emp, _, _, make_type = leave_setup
    kind = await make_type()
    employee_headers = auth_headers(UserRole.EMPLOYEE, employee_id=emp["id"])
    assert (
        await client.get(f"{BASE}/time-off-types/lookup", headers=employee_headers)
    ).status_code == 200
    type_input = {
        key: kind[key]
        for key in (
            "name",
            "code",
            "unit",
            "requires_allocation",
            "requires_approval",
            "payroll_integration",
            "description",
            "version",
        )
    }
    type_input["name"] = "Renamed leave"
    assert (
        await client.patch(
            f"{BASE}/time-off-types/{kind['id']}",
            headers=employee_headers,
            json=type_input,
        )
    ).status_code == 403
    res = await client.patch(
        f"{BASE}/time-off-types/{kind['id']}", headers=HR, json=type_input
    )
    assert res.status_code == 200, res.text
    kind = res.json()
    assert kind["name"] == "Renamed leave"
    alloc = await allocation(client, emp, kind)
    update = {
        "allocated": "3.00",
        "valid_from": "2026-01-01",
        "valid_to": None,
        "status": "confirmed",
        "version": alloc["version"],
    }
    assert (
        await client.patch(
            f"{BASE}/time-off-allocations/{alloc['id']}",
            headers=employee_headers,
            json=update,
        )
    ).status_code == 403
    res = await client.patch(
        f"{BASE}/time-off-allocations/{alloc['id']}", headers=HR, json=update
    )
    assert res.status_code == 200, res.text
    alloc = res.json()
    assert Decimal(alloc["remaining"]) == 3
    assert (
        await client.patch(
            f"{BASE}/time-off-allocations/{alloc['id']}", headers=HR, json=update
        )
    ).status_code == 409
    assert (
        await client.delete(
            f"{BASE}/time-off-allocations/{alloc['id']}?version={alloc['version']}",
            headers=HR,
        )
    ).status_code == 200
    assert (
        await client.get(f"{BASE}/time-off-allocations/{alloc['id']}", headers=HR)
    ).status_code == 404
    # Type keeps its historical policy even when the allocation was archived.
    assert (
        await client.delete(
            f"{BASE}/time-off-types/{kind['id']}?version={kind['version']}", headers=HR
        )
    ).status_code == 409
    unused = await make_type()
    assert (
        await client.delete(
            f"{BASE}/time-off-types/{unused['id']}?version={unused['version']}",
            headers=HR,
        )
    ).status_code == 200
    assert (
        await client.get(f"{BASE}/time-off-types/{unused['id']}", headers=HR)
    ).status_code == 404


async def test_pending_request_update_delete_and_approved_immutability(
    client, leave_setup
):
    emp, _, kind, _ = leave_setup
    alloc = await allocation(client, emp, kind, "5")
    req = await request(client, emp, kind)
    update = {
        "employee_id": emp["id"],
        "time_off_type_id": kind["id"],
        "date_from": DAY,
        "date_to": "2026-09-08",
        "reason": "Two days",
        "version": req["version"],
    }
    res = await client.patch(
        f"{BASE}/time-off-requests/{req['id']}", headers=HR, json=update
    )
    assert res.status_code == 200, res.text
    req = res.json()
    assert Decimal(req["duration"]) == 2
    assert Decimal((await balance(client, alloc))["taken"]) == 0
    res = await decide(client, req)
    assert res.status_code == 200
    req = res.json()
    update["version"] = req["version"]
    assert (
        await client.patch(
            f"{BASE}/time-off-requests/{req['id']}", headers=HR, json=update
        )
    ).status_code == 409
    assert (
        await client.delete(
            f"{BASE}/time-off-requests/{req['id']}?version={req['version']}", headers=HR
        )
    ).status_code == 409
    second = await request(client, emp, kind)
    assert (
        await client.delete(
            f"{BASE}/time-off-requests/{second['id']}?version={second['version']}",
            headers=HR,
        )
    ).status_code == 200
    assert (
        await client.get(f"{BASE}/time-off-requests/{second['id']}", headers=HR)
    ).status_code == 404


async def test_hour_leave_uses_real_schedule_and_attendance_status(
    client, leave_setup, cleanup_schedules
):
    emp, _, _, make_type = leave_setup
    res = await client.post(
        f"{BASE}/working-schedules/",
        headers=HR,
        json={
            "name": "Monday seven hours",
            "lines": [
                {
                    "day_of_week": "monday",
                    "start_time": "09:00",
                    "end_time": "17:00",
                    "break_minutes": 60,
                }
            ],
        },
    )
    assert res.status_code == 201, res.text
    schedule = res.json()
    res = await client.patch(
        f"{BASE}/employees/{emp['id']}",
        headers=HR,
        json={"default_schedule_id": schedule["id"]},
    )
    assert res.status_code == 200
    kind = await make_type(unit="hours")
    alloc = await allocation(client, emp, kind, "14")
    req = await request(client, emp, kind, date_to="2026-09-08")
    assert Decimal(req["duration"]) == 7  # Tuesday is not scheduled.
    assert (await decide(client, req)).status_code == 200
    assert Decimal((await balance(client, alloc))["remaining"]) == 7
    for start, end, expected in [
        ("09:01", "16:00", "late"),
        ("09:00", "17:00", "overtime"),
    ]:
        res = await client.post(
            f"{BASE}/attendance/",
            headers=HR,
            json={
                "employee_id": emp["id"],
                "check_in": f"{DAY}T{start}:00Z",
                "check_out": f"{DAY}T{end}:00Z",
            },
        )
        assert res.status_code == 201, res.text
        assert res.json()["status"] == expected
        fetched = await client.get(f"{BASE}/attendance/{res.json()['id']}", headers=HR)
        assert fetched.status_code == 200 and fetched.json()["status"] == expected


async def test_hour_leave_without_schedule_fails_clearly(client, leave_setup):
    emp, _, _, make_type = leave_setup
    kind = await make_type(unit="hours")
    res = await client.post(
        f"{BASE}/time-off-requests/",
        headers=HR,
        json={
            "employee_id": emp["id"],
            "time_off_type_id": kind["id"],
            "date_from": DAY,
            "date_to": DAY,
        },
    )
    assert res.status_code == 422 and "schedule" in res.text


async def test_refusal_without_allocation_does_not_lookup(
    client, leave_setup, monkeypatch
):
    emp, _, kind, _ = leave_setup
    req = await request(client, emp, kind)

    async def forbidden(*args):
        raise AssertionError("Refusal must not look up allocation")

    monkeypatch.setattr(TimeOffRequestService, "_matching_allocation", forbidden)
    assert (await decide(client, req, "refuse")).status_code == 200


async def test_approval_audit_failure_rolls_back_debit_even_if_caller_commits(
    client, leave_setup, monkeypatch
):
    emp, _, kind, _ = leave_setup
    alloc = await allocation(client, emp, kind)
    req = await request(client, emp, kind)

    async def fail_audit(*args, **kwargs):
        raise RuntimeError("Injected audit failure")

    monkeypatch.setattr(TimeOffRequestService, "audit", fail_audit)
    async with AsyncSessionLocal() as session:
        with pytest.raises(RuntimeError):
            await TimeOffRequestService(session).decide(
                req["id"],
                Decision(version=req["version"]),
                CurrentUser("hr.manager@peoplepay360.com", UserRole.HR_MANAGER),
                approve=True,
            )
        await session.commit()
    assert await balance(client, alloc) == alloc
    assert (
        await client.get(f"{BASE}/time-off-requests/{req['id']}", headers=HR)
    ).json()["status"] == "to_approve"


async def test_concurrent_approval_of_same_request_does_not_double_debit(
    client, leave_setup, monkeypatch
):
    emp, _, kind, _ = leave_setup
    alloc = await allocation(client, emp, kind, "2")
    req = await request(client, emp, kind)
    barrier = asyncio.Barrier(2)
    original = TimeOffRequestService._matching_allocation

    async def synchronized(self, row):
        result = await original(self, row)
        await asyncio.wait_for(barrier.wait(), 10)
        return result

    monkeypatch.setattr(TimeOffRequestService, "_matching_allocation", synchronized)
    results = await asyncio.wait_for(
        asyncio.gather(decide(client, req), decide(client, req)), 20
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    assert Decimal((await balance(client, alloc))["taken"]) == 1


async def test_invalid_dates_and_client_balance_rejected(client, leave_setup):
    emp, _, kind, _ = leave_setup
    res = await client.post(
        f"{BASE}/time-off-requests/",
        headers=HR,
        json={
            "employee_id": emp["id"],
            "time_off_type_id": kind["id"],
            "date_from": DAY,
            "date_to": "2026-09-01",
        },
    )
    assert res.status_code == 422
    base = {
        "employee_id": emp["id"],
        "time_off_type_id": kind["id"],
        "allocated": "2",
        "valid_from": DAY,
    }
    for extra in [
        {"taken": 0},
        {"remaining": 100},
        {"allocated": -1},
        {"valid_to": "2026-09-01"},
    ]:
        res = await client.post(
            f"{BASE}/time-off-allocations/", headers=HR, json={**base, **extra}
        )
        assert res.status_code == 422, res.text
    res = await client.post(
        f"{BASE}/attendance/",
        headers=HR,
        json={
            "employee_id": emp["id"],
            "check_in": f"{DAY}T10:00:00Z",
            "check_out": f"{DAY}T09:00:00Z",
        },
    )
    assert res.status_code == 422
