"""Working schedules (PS A3).

The requirement this file exists to hold down: **weekly hours are auto-computed
and never manually entered.** That is tested three ways — the arithmetic is
correct, a client cannot submit the field, and editing the lines recomputes it.
"""
from decimal import Decimal

import pytest

from app.models.enums import UserRole, Weekday
from app.services.schedule import compute_weekly_hours
from tests.conftest import auth_headers

HR = auth_headers(UserRole.HR_MANAGER)
EMPLOYEE = auth_headers(UserRole.EMPLOYEE)
PAYROLL_MANAGER = auth_headers(UserRole.HR_PAYROLL_MANAGER)

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]


def _line(day: str, start: str = "09:00:00", end: str = "17:00:00", break_minutes: int = 60) -> dict:
    return {"day_of_week": day, "start_time": start, "end_time": end, "break_minutes": break_minutes}


def _standard_week() -> list[dict]:
    """5 days x (8h block - 1h break) = 35.00 weekly hours."""
    return [_line(day) for day in WEEKDAYS]


async def _create(client, headers=HR, **overrides) -> dict:
    body = {"name": "Standard 35h", "schedule_type": "full_time", "lines": _standard_week()}
    body.update(overrides)
    resp = await client.post("/api/v1/working-schedules/", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ------------------------------------------------------- the computation

async def test_weekly_hours_are_computed_from_the_lines(client, cleanup_schedules):
    schedule = await _create(client)
    # 5 days, 8h each, minus a 1h break each.
    assert Decimal(schedule["weekly_hours"]) == Decimal("35.00")
    assert len(schedule["lines"]) == 5


async def test_weekly_hours_ignore_a_client_supplied_value(client, cleanup_schedules):
    """The field is not on the request schema at all, so the strongest possible
    outcome is that sending it is rejected outright rather than silently
    ignored — `extra="forbid"` makes that a 422."""
    resp = await client.post(
        "/api/v1/working-schedules/",
        json={"name": "Liar", "lines": _standard_week(), "weekly_hours": "99.00"},
        headers=HR,
    )
    assert resp.status_code == 422, resp.text


async def test_editing_the_lines_recomputes_weekly_hours(client, cleanup_schedules):
    schedule = await _create(client)
    assert Decimal(schedule["weekly_hours"]) == Decimal("35.00")

    resp = await client.patch(
        f"/api/v1/working-schedules/{schedule['id']}",
        json={"lines": [_line("monday"), _line("tuesday")]},
        headers=HR,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["weekly_hours"]) == Decimal("14.00")
    assert len(body["lines"]) == 2


async def test_a_schedule_with_no_lines_is_zero_hours(client, cleanup_schedules):
    schedule = await _create(client, lines=[])
    assert Decimal(schedule["weekly_hours"]) == Decimal("0.00")


async def test_split_shifts_on_one_day_are_supported_and_summed(client, cleanup_schedules):
    """Two blocks on one day is the whole reason multiple lines per day exist —
    the gap between them is the unpaid break."""
    schedule = await _create(
        client,
        lines=[
            _line("monday", "09:00:00", "13:00:00", break_minutes=0),
            _line("monday", "14:00:00", "18:00:00", break_minutes=0),
        ],
    )
    assert Decimal(schedule["weekly_hours"]) == Decimal("8.00")


def test_compute_weekly_hours_sums_minutes_before_converting():
    """Awkward durations must not accumulate rounding error.

    Five 7h35m days is 37h55m = 37.9166… hours. Rounding each day to two
    decimals first (7.58 x 5 = 37.90) loses a minute a week, which over a year
    is an hour of unpaid time. Minutes are summed as integers and divided once.
    """

    class _Line:
        def __init__(self, start, end, brk):
            from datetime import time

            self.start_time = time.fromisoformat(start)
            self.end_time = time.fromisoformat(end)
            self.break_minutes = brk

    lines = [_Line("09:00", "17:05", 30) for _ in range(5)]  # 8h05 - 30m = 7h35 per day
    assert compute_weekly_hours(lines) == Decimal("37.92")  # 2275 minutes / 60, half-even


def test_compute_weekly_hours_is_decimal_never_float():
    """Architecture §10. Weekly hours multiply into an hourly rate."""
    result = compute_weekly_hours([])
    assert isinstance(result, Decimal)


# --------------------------------------------------------------- validation

async def test_a_line_ending_before_it_starts_is_rejected(client, cleanup_schedules):
    resp = await client.post(
        "/api/v1/working-schedules/",
        json={"name": "Backwards", "lines": [_line("monday", "17:00:00", "09:00:00")]},
        headers=HR,
    )
    assert resp.status_code == 422, resp.text


async def test_a_break_longer_than_the_block_is_rejected(client, cleanup_schedules):
    """Otherwise the line contributes zero or negative hours, which is either a
    typo or an attempt to drag a schedule's total down."""
    resp = await client.post(
        "/api/v1/working-schedules/",
        json={"name": "All break", "lines": [_line("monday", "09:00:00", "10:00:00", break_minutes=90)]},
        headers=HR,
    )
    assert resp.status_code == 422, resp.text


async def test_overlapping_blocks_on_the_same_day_are_rejected(client, cleanup_schedules):
    """Overlapping blocks would double-count the overlap into weekly_hours —
    and weekly_hours feeds payroll, so the schedule would claim hours nobody
    worked."""
    resp = await client.post(
        "/api/v1/working-schedules/",
        json={
            "name": "Overlapping",
            "lines": [
                _line("monday", "09:00:00", "13:00:00", break_minutes=0),
                _line("monday", "12:00:00", "17:00:00", break_minutes=0),
            ],
        },
        headers=HR,
    )
    assert resp.status_code == 422, resp.text
    assert "overlap" in resp.text.lower()


# ------------------------------------------------------------------- CRUD

async def test_crud_round_trip(client, cleanup_schedules):
    created = await _create(client, name="Part time 20h")
    public_id = created["id"]
    assert public_id.startswith("wsch_")

    fetched = await client.get(f"/api/v1/working-schedules/{public_id}", headers=HR)
    assert fetched.status_code == 200
    assert fetched.json()["name"] == "Part time 20h"

    renamed = await client.patch(
        f"/api/v1/working-schedules/{public_id}", json={"name": "Part time (revised)"}, headers=HR
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "Part time (revised)"
    # Lines were not sent, so they and the computed total are untouched.
    assert Decimal(renamed.json()["weekly_hours"]) == Decimal("35.00")
    assert len(renamed.json()["lines"]) == 5

    deleted = await client.delete(f"/api/v1/working-schedules/{public_id}", headers=HR)
    assert deleted.status_code == 200
    assert (await client.get(f"/api/v1/working-schedules/{public_id}", headers=HR)).status_code == 404


async def test_lines_come_back_in_weekday_order(client, cleanup_schedules):
    """Stored in canonical order so the form renders Monday-first without the
    client sorting, and two identical patterns have identical row order."""
    schedule = await _create(client, lines=[_line("friday"), _line("monday"), _line("wednesday")])
    assert [line["day_of_week"] for line in schedule["lines"]] == ["monday", "wednesday", "friday"]


async def test_a_schedule_can_be_assigned_to_an_employee(client, cleanup_schedules, cleanup_employees):
    """PS A3: assignable to an Employee (and overridable per Contract)."""
    from tests.conftest import unique_email

    schedule = await _create(client)
    created = await client.post(
        "/api/v1/employees/",
        json={
            "first_name": "Scheduled",
            "last_name": "Person",
            "work_email": unique_email("scheduled"),
            "default_schedule_id": schedule["id"],
        },
        headers=HR,
    )
    assert created.status_code == 201, created.text
    assert created.json()["default_schedule"]["id"] == schedule["id"]
    assert Decimal(str(created.json()["default_schedule"]["weekly_hours"])) == Decimal("35.00")


# ------------------------------------------------------------------- RBAC

async def test_employee_role_is_denied_every_schedule_operation(client, cleanup_schedules):
    schedule = await _create(client)

    assert (await client.get("/api/v1/working-schedules/", headers=EMPLOYEE)).status_code == 403
    assert (
        await client.post("/api/v1/working-schedules/", json={"name": "X", "lines": []}, headers=EMPLOYEE)
    ).status_code == 403
    assert (
        await client.patch(
            f"/api/v1/working-schedules/{schedule['id']}", json={"name": "X"}, headers=EMPLOYEE
        )
    ).status_code == 403
    assert (
        await client.delete(f"/api/v1/working-schedules/{schedule['id']}", headers=EMPLOYEE)
    ).status_code == 403


@pytest.mark.parametrize("headers", [HR, PAYROLL_MANAGER, auth_headers(UserRole.ADMIN)])
async def test_hr_roles_and_above_may_manage_schedules(client, headers, cleanup_schedules):
    resp = await client.get("/api/v1/working-schedules/", headers=headers)
    assert resp.status_code == 200, resp.text
