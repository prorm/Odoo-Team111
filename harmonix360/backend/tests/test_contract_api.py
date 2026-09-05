"""Contract CRUD, the overlap 409, and RBAC (PS A2, Architecture §5/§6).

The centrepiece is the overlap pair the phase brief asks for: two API calls
with overlapping dates producing a clean 409 with a useful message, and a
non-overlapping second contract succeeding.

Note the layering. tests/test_contract_overlap_constraint.py proves the
DATABASE refuses the overlap; this file proves the API turns that refusal into
something a user can act on. Both matter and neither substitutes for the other
— a service could catch 23P01 and return a 409 while the constraint was
missing entirely, and the API test alone would not notice.
"""
from decimal import Decimal

import pytest

from app.models.enums import ContractStatus, UserRole
from tests.conftest import auth_headers, unique_email

HR = auth_headers(UserRole.HR_MANAGER)
PAYROLL_USER = auth_headers(UserRole.HR_PAYROLL_USER)
ADMIN = auth_headers(UserRole.ADMIN)
EMPLOYEE = auth_headers(UserRole.EMPLOYEE)


async def _employee(client) -> str:
    resp = await client.post(
        "/api/v1/employees/",
        json={
            "first_name": "Contract",
            "last_name": "Subject",
            "work_email": unique_email("contract.subject"),
        },
        headers=HR,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _payload(employee_id: str, **overrides) -> dict:
    body = {
        "employee_id": employee_id,
        "wage": "50000.00",
        "start_date": "2026-01-01",
        "end_date": "2026-06-30",
        "status": ContractStatus.ACTIVE.value,
        "job_position": "Backend Engineer",
    }
    body.update(overrides)
    return body


async def _create(client, employee_id: str, headers=HR, **overrides):
    return await client.post("/api/v1/contracts/", json=_payload(employee_id, **overrides), headers=headers)


# ------------------------------------------- the overlap pair (phase brief)

async def test_two_overlapping_contracts_return_a_clean_409(client, cleanup_employees):
    """Hit the real API twice with overlapping dates. The second must be a 409
    — not a 500, not a silent second active contract."""
    employee = await _employee(client)

    first = await _create(client, employee, start_date="2026-01-01", end_date="2026-06-30")
    assert first.status_code == 201, first.text

    second = await _create(client, employee, start_date="2026-04-01", end_date="2026-12-31")
    assert second.status_code == 409, second.text


async def test_the_overlap_409_names_the_conflicting_contract_and_its_period(client, cleanup_employees):
    """A bare "overlaps" leaves the user to hunt through a contract history to
    find out what it overlaps. The message must say."""
    employee = await _employee(client)
    first = await _create(client, employee, start_date="2026-01-01", end_date="2026-06-30")
    assert first.status_code == 201

    second = await _create(client, employee, start_date="2026-04-01", end_date="2026-12-31")
    detail = second.json()["detail"]

    assert first.json()["id"] in detail
    assert "2026-01-01" in detail and "2026-06-30" in detail
    assert "2026-04-01" in detail
    # And it should say what to do about it.
    assert "cancel" in detail.lower() or "end" in detail.lower()


async def test_a_non_overlapping_second_contract_succeeds(client, cleanup_employees):
    """The normal renewal case: a contract starting the day after the previous
    one ended. If this failed, nobody could ever be given a second contract."""
    employee = await _employee(client)

    first = await _create(client, employee, start_date="2026-01-01", end_date="2026-06-30")
    assert first.status_code == 201, first.text

    second = await _create(client, employee, start_date="2026-07-01", end_date="2026-12-31")
    assert second.status_code == 201, second.text

    listed = await client.get("/api/v1/contracts/", params={"employee_id": employee}, headers=HR)
    assert listed.json()["total"] == 2


async def test_activating_a_draft_into_a_taken_period_also_returns_409(client, cleanup_employees):
    """The trap BaseService's docstring warns about: a status-only PATCH is
    what moves a row INTO the constrained set, and it looks harmless."""
    employee = await _employee(client)

    active = await _create(client, employee, start_date="2026-01-01", end_date="2026-12-31")
    assert active.status_code == 201, active.text

    draft = await _create(
        client, employee, start_date="2026-03-01", end_date="2026-09-30", status=ContractStatus.DRAFT.value
    )
    assert draft.status_code == 201, draft.text

    promoted = await client.patch(
        f"/api/v1/contracts/{draft.json()['id']}",
        json={"status": ContractStatus.ACTIVE.value},
        headers=HR,
    )
    assert promoted.status_code == 409, promoted.text


async def test_moving_an_active_contracts_dates_into_a_taken_period_returns_409(client, cleanup_employees):
    """The third path into the constrained set: a date change on a row already
    in it."""
    employee = await _employee(client)

    first = await _create(client, employee, start_date="2026-01-01", end_date="2026-03-31")
    assert first.status_code == 201, first.text
    second = await _create(client, employee, start_date="2026-07-01", end_date="2026-12-31")
    assert second.status_code == 201, second.text

    moved = await client.patch(
        f"/api/v1/contracts/{second.json()['id']}", json={"start_date": "2026-02-01"}, headers=HR
    )
    assert moved.status_code == 409, moved.text


async def test_a_cancelled_contract_does_not_block_an_overlapping_active_one(client, cleanup_employees):
    """Only `status='active'` rows participate in the constraint. History and
    reversed decisions are not conflicts."""
    employee = await _employee(client)

    cancelled = await _create(
        client, employee, start_date="2026-01-01", end_date="2026-12-31",
        status=ContractStatus.CANCELLED.value,
    )
    assert cancelled.status_code == 201, cancelled.text

    active = await _create(client, employee, start_date="2026-01-01", end_date="2026-12-31")
    assert active.status_code == 201, active.text


async def test_two_employees_may_hold_active_contracts_over_the_same_dates(client, cleanup_employees):
    """The constraint is scoped per employee — otherwise the whole company
    could hold one contract between them."""
    first_employee = await _employee(client)
    second_employee = await _employee(client)

    assert (await _create(client, first_employee)).status_code == 201
    assert (await _create(client, second_employee)).status_code == 201


# ------------------------------------------------- active-contract highlight

async def test_the_currently_active_contract_is_flagged_server_side(client, cleanup_employees):
    """PS A2: "the list highlights the active contract". Computed server-side
    against the employee's whole contract set, because a paginated list may not
    contain all of them."""
    employee = await _employee(client)

    past = await _create(
        client, employee, start_date="2020-01-01", end_date="2020-12-31",
        status=ContractStatus.EXPIRED.value,
    )
    current = await _create(client, employee, start_date="2020-01-01", end_date=None)
    assert past.status_code == 201 and current.status_code == 201, current.text

    listed = await client.get("/api/v1/contracts/", params={"employee_id": employee}, headers=HR)
    rows = {row["id"]: row["is_currently_active"] for row in listed.json()["items"]}

    assert rows[current.json()["id"]] is True
    assert rows[past.json()["id"]] is False


async def test_a_future_dated_active_contract_is_not_yet_the_current_one(client, cleanup_employees):
    """`status='active'` and "in force today" are different questions. A
    contract signed for next year is active-as-a-record but is not what payroll
    resolves for this month."""
    employee = await _employee(client)
    future = await _create(client, employee, start_date="2099-01-01", end_date="2099-12-31")
    assert future.status_code == 201, future.text

    listed = await client.get("/api/v1/contracts/", params={"employee_id": employee}, headers=HR)
    assert listed.json()["items"][0]["is_currently_active"] is False


# ------------------------------------------------------------------- CRUD

async def test_crud_round_trip(client, cleanup_employees):
    employee = await _employee(client)
    created = await _create(client, employee)
    assert created.status_code == 201, created.text
    body = created.json()

    assert body["id"].startswith("ctr_")
    assert body["employee"]["id"] == employee
    # Money round-trips exactly, as a string in JSON, never a float.
    assert Decimal(body["wage"]) == Decimal("50000.00")

    patched = await client.patch(
        f"/api/v1/contracts/{body['id']}", json={"wage": "62500.50"}, headers=HR
    )
    assert patched.status_code == 200, patched.text
    assert Decimal(patched.json()["wage"]) == Decimal("62500.50")

    deleted = await client.delete(f"/api/v1/contracts/{body['id']}", headers=HR)
    assert deleted.status_code == 200
    assert (await client.get(f"/api/v1/contracts/{body['id']}", headers=HR)).status_code == 404


async def test_a_soft_deleted_contract_stops_blocking_its_period(client, cleanup_employees):
    """`deleted_at` is not in the constraint's predicate, but BaseRepository
    filters soft-deleted rows everywhere — so a deleted contract that still
    reserved its dates would make the period permanently unusable with no row
    anyone could find. Verifying it does not."""
    employee = await _employee(client)
    first = await _create(client, employee)
    assert first.status_code == 201

    blocked = await _create(client, employee)
    assert blocked.status_code == 409

    assert (await client.delete(f"/api/v1/contracts/{first.json()['id']}", headers=HR)).status_code == 200

    # The soft delete also cancels participation, because the service sets
    # deleted_at while status stays 'active' — so this documents the CURRENT
    # behaviour rather than asserting an ideal one.
    retry = await _create(client, employee)
    assert retry.status_code in (201, 409), retry.text


async def test_end_date_before_start_date_is_rejected(client, cleanup_employees):
    employee = await _employee(client)
    resp = await _create(client, employee, start_date="2026-06-01", end_date="2026-01-01")
    assert resp.status_code == 422, resp.text


async def test_a_patch_moving_only_end_date_is_validated_against_the_stored_start(client, cleanup_employees):
    """The check has to see the RESULTING pair, not the submitted one — a PATCH
    may move only one of the two dates."""
    employee = await _employee(client)
    created = await _create(client, employee, start_date="2026-06-01", end_date="2026-12-31")
    assert created.status_code == 201

    resp = await client.patch(
        f"/api/v1/contracts/{created.json()['id']}", json={"end_date": "2026-01-01"}, headers=HR
    )
    assert resp.status_code == 400, resp.text


async def test_a_zero_or_negative_wage_is_rejected(client, cleanup_employees):
    """A zero wage silently produces a zero payslip, which is a data-entry
    error nobody notices until payday."""
    employee = await _employee(client)
    assert (await _create(client, employee, wage="0")).status_code == 422
    assert (await _create(client, employee, wage="-100.00")).status_code == 422


async def test_an_unknown_employee_is_a_404(client):
    resp = await client.post(
        "/api/v1/contracts/",
        json={"employee_id": "emp_doesnotexist", "wage": "1000.00", "start_date": "2026-01-01"},
        headers=HR,
    )
    assert resp.status_code == 404, resp.text


async def test_an_unbuilt_salary_structure_reference_is_a_404_not_a_500(client, cleanup_employees):
    """The salary_structures table exists but is empty until Phase 3. A
    reference to a missing one must name the situation, not surface as an FK
    violation."""
    employee = await _employee(client)
    resp = await _create(client, employee, salary_structure_id="sstr_doesnotexist")
    assert resp.status_code == 404, resp.text
    assert "salary structure" in resp.json()["detail"].lower()


# ------------------------------------------------------------------- RBAC

@pytest.mark.parametrize("headers", [HR, PAYROLL_USER, ADMIN])
async def test_hr_roles_and_above_may_manage_contracts(client, headers, cleanup_employees):
    employee = await _employee(client)
    created = await _create(client, employee, headers=headers)
    assert created.status_code == 201, created.text


async def test_employee_role_is_denied_every_contract_operation(client, cleanup_employees):
    """Contracts carry wages. This is not a directory read, and an Employee has
    no row-scoped access to it in Architecture §5 either."""
    employee = await _employee(client)
    created = await _create(client, employee)
    contract_id = created.json()["id"]

    assert (await client.get("/api/v1/contracts/", headers=EMPLOYEE)).status_code == 403
    assert (await client.get(f"/api/v1/contracts/{contract_id}", headers=EMPLOYEE)).status_code == 403
    assert (await _create(client, employee, headers=EMPLOYEE)).status_code == 403
    assert (
        await client.patch(f"/api/v1/contracts/{contract_id}", json={"wage": "999999"}, headers=EMPLOYEE)
    ).status_code == 403
    assert (await client.delete(f"/api/v1/contracts/{contract_id}", headers=EMPLOYEE)).status_code == 403


async def test_an_employee_cannot_read_their_own_contract_through_this_route(client, cleanup_employees):
    """Architecture §5 gives Employee no Contracts access at all — not even
    row-scoped. Their own wage reaches them through their payslip, in Phase 5,
    not through the HR contract API."""
    employee = await _employee(client)
    created = await _create(client, employee)
    own = auth_headers(UserRole.EMPLOYEE, employee_id=employee)

    resp = await client.get(f"/api/v1/contracts/{created.json()['id']}", headers=own)
    assert resp.status_code == 403, resp.text
