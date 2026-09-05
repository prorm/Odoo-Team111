"""Employee CRUD and its RBAC (PS A1/B2, Architecture §5).

Every test goes through the real API against the real database. The RBAC tests
assert BOTH sides for every role that matters — an allow-only suite would pass
just as happily against a router with no role check at all.
"""
import pytest

from app.models.enums import EmployeeStatus, EmployeeType, UserRole
from tests.conftest import auth_headers, unique_email

HR = auth_headers(UserRole.HR_MANAGER)
PAYROLL_USER = auth_headers(UserRole.HR_PAYROLL_USER)
PAYROLL_MANAGER = auth_headers(UserRole.HR_PAYROLL_MANAGER)
ADMIN = auth_headers(UserRole.ADMIN)
EMPLOYEE = auth_headers(UserRole.EMPLOYEE)


def _payload(**overrides) -> dict:
    body = {
        "first_name": "Rahul",
        "last_name": "Verma",
        "work_email": unique_email("rahul"),
        "job_position": "Backend Engineer",
        "employee_type": EmployeeType.PERMANENT.value,
        "status": EmployeeStatus.ACTIVE.value,
    }
    body.update(overrides)
    return body


async def _create(client, headers=HR, **overrides) -> dict:
    resp = await client.post("/api/v1/employees/", json=_payload(**overrides), headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ------------------------------------------------------------------- CRUD

async def test_create_returns_a_prefixed_public_id_never_an_integer(client, cleanup_employees):
    body = await _create(client)
    assert body["id"].startswith("emp_")
    # The integer primary key is sequential; leaking it would let anyone count
    # the workforce and address rows they were never given.
    assert not body["id"].removeprefix("emp_").isdigit()
    assert body["version"] == 1


async def test_create_read_update_delete_round_trip(client, cleanup_employees):
    created = await _create(client, first_name="Priya", last_name="Sharma")
    public_id = created["id"]

    fetched = await client.get(f"/api/v1/employees/{public_id}", headers=HR)
    assert fetched.status_code == 200
    assert fetched.json()["full_name"] == "Priya Sharma"

    patched = await client.patch(
        f"/api/v1/employees/{public_id}",
        json={"job_position": "Engineering Manager"},
        headers=HR,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["job_position"] == "Engineering Manager"
    # Optimistic concurrency actually moved.
    assert patched.json()["version"] > created["version"]

    deleted = await client.delete(f"/api/v1/employees/{public_id}", headers=HR)
    assert deleted.status_code == 200

    gone = await client.get(f"/api/v1/employees/{public_id}", headers=HR)
    assert gone.status_code == 404


async def test_patch_leaves_omitted_fields_alone(client, cleanup_employees):
    """A PATCH is not a PUT. Omitting a field must preserve it — the bug this
    guards against wipes an employee's bank details on an unrelated edit."""
    created = await _create(client, bank_account="GB33BUKB20201555555555", phone="+44 20 7946 0000")

    patched = await client.patch(
        f"/api/v1/employees/{created['id']}", json={"job_position": "Staff Engineer"}, headers=HR
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["bank_account"] == "GB33BUKB20201555555555"
    assert body["phone"] == "+44 20 7946 0000"


async def test_patch_can_explicitly_clear_a_nullable_reference(client, cleanup_employees, department):
    """The other half of the same rule: an explicit null must CLEAR, so
    "omitted" and "set to null" have to be distinguishable."""
    created = await _create(client, department_id=department)
    assert created["department"] is not None

    patched = await client.patch(
        f"/api/v1/employees/{created['id']}", json={"department_id": None}, headers=HR
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["department"] is None


async def test_duplicate_work_email_returns_409_naming_the_field(client, cleanup_employees):
    email = unique_email("duplicate")
    await _create(client, work_email=email)

    resp = await client.post("/api/v1/employees/", json=_payload(work_email=email), headers=HR)
    assert resp.status_code == 409, resp.text
    assert email in resp.json()["detail"]


async def test_work_email_is_normalised_to_lowercase(client, cleanup_employees):
    """The (work_email, tenant_id) unique constraint is case-sensitive in
    Postgres, so without normalisation 'R.Verma@x' and 'r.verma@x' are two
    employees who are one person."""
    email = unique_email("MixedCase")
    created = await _create(client, work_email=email.upper())
    assert created["work_email"] == email.lower()


async def test_department_and_manager_are_resolved_and_returned(client, cleanup_employees, department):
    manager = await _create(client, first_name="Meera", last_name="Iyer")
    report = await _create(client, department_id=department, manager_id=manager["id"])

    assert report["department"]["id"] == department
    assert report["manager"]["id"] == manager["id"]
    assert report["manager"]["last_name"] == "Iyer"


async def test_an_unknown_reference_is_a_404_not_a_500(client, cleanup_employees):
    resp = await client.post(
        "/api/v1/employees/", json=_payload(department_id="dept_doesnotexist"), headers=HR
    )
    assert resp.status_code == 404, resp.text


async def test_a_public_id_of_the_wrong_entity_type_is_rejected(client, cleanup_employees, department):
    """Prefixes are what stop one entity's id resolving to another's row. A
    department id in the manager field must not find an employee."""
    resp = await client.post("/api/v1/employees/", json=_payload(manager_id=department), headers=HR)
    assert resp.status_code == 404, resp.text


async def test_an_employee_cannot_be_their_own_manager(client, cleanup_employees):
    created = await _create(client)
    resp = await client.patch(
        f"/api/v1/employees/{created['id']}", json={"manager_id": created["id"]}, headers=HR
    )
    assert resp.status_code == 400, resp.text


# ------------------------------------------------------------ list & search

async def test_search_matches_name_email_and_position(client, cleanup_employees):
    await _create(client, first_name="Zubin", last_name="Mehta", job_position="Conductor")

    for term in ("Zubin", "Mehta", "Conductor"):
        resp = await client.get("/api/v1/employees/", params={"search": term}, headers=HR)
        assert resp.status_code == 200, resp.text
        assert any(row["first_name"] == "Zubin" for row in resp.json()["items"]), term


async def test_list_filters_by_department_and_status(client, cleanup_employees, department):
    await _create(client, department_id=department, status=EmployeeStatus.ON_LEAVE.value)
    await _create(client, department_id=department, status=EmployeeStatus.ACTIVE.value)

    resp = await client.get(
        "/api/v1/employees/",
        params={"department_id": department, "status": EmployeeStatus.ON_LEAVE.value},
        headers=HR,
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == EmployeeStatus.ON_LEAVE.value


async def test_lookup_excludes_the_employee_being_edited(client, cleanup_employees):
    """Nobody may be offered as their own manager, and the server enforces it
    rather than trusting the picker to filter."""
    subject = await _create(client, first_name="Loopy", last_name="Candidate")

    resp = await client.get(
        "/api/v1/employees/lookup", params={"search": "Loopy", "exclude": subject["id"]}, headers=HR
    )
    assert resp.status_code == 200, resp.text
    assert all(row["id"] != subject["id"] for row in resp.json())


async def test_lookup_returns_only_reference_fields(client, cleanup_employees):
    """A picker lists every colleague, so its payload must not carry bank
    details or phone numbers."""
    await _create(client, first_name="Picker", bank_account="SECRET-ACCOUNT", phone="+1 555 0000")

    resp = await client.get("/api/v1/employees/lookup", params={"search": "Picker"}, headers=HR)
    assert resp.status_code == 200
    row = resp.json()[0]
    assert "bank_account" not in row
    assert "phone" not in row


# ------------------------------------------------------- smart buttons (B2)

async def test_smart_button_counts_start_at_zero_and_track_contracts(client, cleanup_employees):
    """Attendance and Time Off have no data until Phases 2-3; they are wired
    now so those phases only supply rows, not plumbing."""
    employee = await _create(client)

    resp = await client.get(f"/api/v1/employees/{employee['id']}/counts", headers=HR)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "contracts": 0,
        "attendance": 0,
        "time_off_requests": 0,
        "time_off_allocations": 0,
    }

    created = await client.post(
        "/api/v1/contracts/",
        json={"employee_id": employee["id"], "wage": "50000.00", "start_date": "2026-01-01"},
        headers=HR,
    )
    assert created.status_code == 201, created.text

    resp = await client.get(f"/api/v1/employees/{employee['id']}/counts", headers=HR)
    assert resp.json()["contracts"] == 1


# ------------------------------------------------------------------- RBAC

@pytest.mark.parametrize(
    "headers,label",
    [(HR, "hr_manager"), (PAYROLL_USER, "hr_payroll_user"), (PAYROLL_MANAGER, "hr_payroll_manager"), (ADMIN, "admin")],
)
async def test_hr_roles_and_above_may_list_employees(client, headers, label):
    resp = await client.get("/api/v1/employees/", headers=headers)
    assert resp.status_code == 200, f"{label} was denied: {resp.text}"


async def test_employee_role_may_not_list_employees(client):
    """The whole staff directory — job titles, work emails, bank accounts — is
    not an employee-level read."""
    resp = await client.get("/api/v1/employees/", headers=EMPLOYEE)
    assert resp.status_code == 403, resp.text


async def test_employee_role_may_not_create_update_or_delete(client, cleanup_employees):
    created = await _create(client)

    assert (await client.post("/api/v1/employees/", json=_payload(), headers=EMPLOYEE)).status_code == 403
    assert (
        await client.patch(
            f"/api/v1/employees/{created['id']}", json={"job_position": "CEO"}, headers=EMPLOYEE
        )
    ).status_code == 403
    assert (await client.delete(f"/api/v1/employees/{created['id']}", headers=EMPLOYEE)).status_code == 403


async def test_an_employee_may_read_their_own_record(client, cleanup_employees):
    """Architecture §5, first row. Identity comes from the token's signed
    `employee_id` claim, never from the request."""
    created = await _create(client)
    own = auth_headers(UserRole.EMPLOYEE, employee_id=created["id"])

    resp = await client.get(f"/api/v1/employees/{created['id']}", headers=own)
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == created["id"]


async def test_an_employee_may_not_read_a_colleagues_record(client, cleanup_employees):
    """404, not 403. A 403 confirms the record exists, which would let anyone
    with a login enumerate the directory by probing ids."""
    mine = await _create(client)
    theirs = await _create(client)
    own = auth_headers(UserRole.EMPLOYEE, employee_id=mine["id"])

    resp = await client.get(f"/api/v1/employees/{theirs['id']}", headers=own)
    assert resp.status_code == 404, resp.text


async def test_an_employee_may_not_read_a_colleagues_smart_button_counts(client, cleanup_employees):
    """How many contracts someone holds is information about them, so the
    counts endpoint is scoped exactly like the record."""
    mine = await _create(client)
    theirs = await _create(client)
    own = auth_headers(UserRole.EMPLOYEE, employee_id=mine["id"])

    assert (await client.get(f"/api/v1/employees/{theirs['id']}/counts", headers=own)).status_code == 404
    assert (await client.get(f"/api/v1/employees/{mine['id']}/counts", headers=own)).status_code == 200


async def test_employees_me_resolves_from_the_token(client, cleanup_employees):
    created = await _create(client)
    own = auth_headers(UserRole.EMPLOYEE, employee_id=created["id"])

    resp = await client.get("/api/v1/employees/me", headers=own)
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == created["id"]


async def test_employees_me_is_404_for_a_login_with_no_employee_record(client):
    resp = await client.get("/api/v1/employees/me", headers=auth_headers(UserRole.HR_PAYROLL_MANAGER))
    assert resp.status_code == 404


async def test_an_employee_may_not_edit_their_own_record(client, cleanup_employees):
    """Self-service read, not self-service write. Changing your own job
    position, status or bank account is exactly the edit that must go through
    HR."""
    created = await _create(client)
    own = auth_headers(UserRole.EMPLOYEE, employee_id=created["id"])

    resp = await client.patch(
        f"/api/v1/employees/{created['id']}", json={"bank_account": "MY-OWN-ACCOUNT"}, headers=own
    )
    assert resp.status_code == 403, resp.text
