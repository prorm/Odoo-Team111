"""Payroll dashboard (PS A7 / B9, Architecture §5's payroll-read boundary).

The dataset below is hand-designed so every aggregate can be checked by hand
rather than by re-deriving the same formula the service uses:

  Department   Employee  Type       Contract        Payslip (status, net)   Schedule (5-day)  Attendance (Mon-Fri)
  Engineering  alice     permanent  active, open     paid,      3000.00     yes               P P P . .   (3 present)
  Engineering  bob       permanent  active, open     paid,      4000.00     yes               P P P P P   (5 present)
  Sales        carol     contract   active, expires  paid,      2000.00     yes               P P P P A   (4 present, 1 absent)
                          in 10 days (-> "expiring")
  Engineering  dave      intern     NONE              (none)                no                (none)
                          (-> "missing_contract")
  Engineering  erin      permanent  active, open     CANCELLED, 9999.00     no                (none)
  Engineering  frank     permanent  active, open     VALIDATED,  500.00     no                (none)

All three PAID payslips sit on ONE Payrun, period 2026-09-01..2026-09-30.
Filter window used throughout: 2026-09-07 (Mon) .. 2026-09-13 (Sun) — a
single calendar week, so "5 expected working days" (Mon-Fri) is exact.

Hand-computed totals used below:
  total_net_salary_paid (unfiltered) = 3000 + 4000 + 2000 = 9000.00
  payslips_generated    (unfiltered) = alice, bob, carol (paid) + frank (validated) = 4
                                        (erin's CANCELLED payslip excluded)
  average_salary        (unfiltered) = (3000 + 4000 + 2000) / 3 = 3000.00
  attendance expected/attended (unfiltered) = 15 / 12 = 80.00%
    (alice 5/3, bob 5/5, carol 5/4; dave/erin/frank have no schedule -> 0/0)

The Payrun/Payslip rows are inserted directly through their repositories
(exactly like `tests/conftest.py`'s `department` fixture inserts a
Department) because Phase 4's compute endpoint does not exist on this branch
yet — see docs/dashboard-phase4-integration.md. This seeds the exact shape
Phase 4 will eventually write; it does not simulate or duplicate payroll
computation.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.core.security import decode_public_id
from app.models.contract import Contract
from app.models.department import Department
from app.models.employee import Employee
from app.models.enums import ContractStatus, PayrunStatus, PayslipStatus, UserRole
from app.models.payroll import Payrun, Payslip
from app.models.salary import SalaryStructure
from app.models.time_off import TimeOffAllocation, TimeOffRequest, TimeOffType
from app.repositories.hr import (
    DepartmentRepository,
    EmployeeRepository,
    PayrunRepository,
    PayslipRepository,
    SalaryStructureRepository,
)
from tests.conftest import auth_headers, unique_email

BASE = "/api/v1"
EMPLOYEE = auth_headers(UserRole.EMPLOYEE)
HR_MANAGER = auth_headers(UserRole.HR_MANAGER)
PAYROLL_USER = auth_headers(UserRole.HR_PAYROLL_USER)
PAYROLL_MANAGER = auth_headers(UserRole.HR_PAYROLL_MANAGER)
ADMIN = auth_headers(UserRole.ADMIN)

PERIOD_START = "2026-09-07"
PERIOD_END = "2026-09-13"
STANDARD_WEEK = [
    {"day_of_week": day, "start_time": "09:00:00", "end_time": "17:00:00", "break_minutes": 60}
    for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]
]


async def _department(name: str) -> str:
    async with AsyncSessionLocal() as s:
        dept = await DepartmentRepository(s).create(
            Department(public_id="temp", name=name, code=f"DASH-{uuid.uuid4().hex[:6]}")
        )
        await s.commit()
        return dept.public_id


async def _schedule(client) -> str:
    resp = await client.post(
        f"{BASE}/working-schedules/",
        json={"name": f"Standard {uuid.uuid4().hex[:6]}", "schedule_type": "full_time", "lines": STANDARD_WEEK},
        headers=HR_MANAGER,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _employee(client, *, department_id, employee_type, schedule_id=None, name="Emp") -> dict:
    body = {
        "first_name": name,
        "last_name": "Test",
        "work_email": unique_email(name.lower()),
        "employee_type": employee_type,
        "department_id": department_id,
    }
    if schedule_id:
        body["default_schedule_id"] = schedule_id
    resp = await client.post(f"{BASE}/employees/", json=body, headers=HR_MANAGER)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _contract(client, employee_id: str, *, end_date=None) -> dict:
    body = {
        "employee_id": employee_id,
        "wage": "50000.00",
        "start_date": "2026-01-01",
        "end_date": end_date,
        "status": ContractStatus.ACTIVE.value,
    }
    resp = await client.post(f"{BASE}/contracts/", json=body, headers=HR_MANAGER)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _attendance(client, employee_id: str, day: str, *, absent: bool = False) -> dict:
    # 09:00-16:00 is exactly the schedule's 7 net hours (8h block - 1h break)
    # so the status comes back PRESENT, not OVERTIME (see derive_attendance_status).
    check_in = f"{day}T09:00:00Z"
    check_out = check_in if absent else f"{day}T16:00:00Z"
    resp = await client.post(
        f"{BASE}/attendance/",
        json={"employee_id": employee_id, "check_in": check_in, "check_out": check_out},
        headers=HR_MANAGER,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest_asyncio.fixture
async def dashboard_dataset(client, cleanup_employees, cleanup_schedules, cleanup_salary_config):
    engineering = await _department("Dashboard Engineering")
    sales = await _department("Dashboard Sales")
    schedule = await _schedule(client)

    alice = await _employee(client, department_id=engineering, employee_type="permanent", schedule_id=schedule, name="Alice")
    bob = await _employee(client, department_id=engineering, employee_type="permanent", schedule_id=schedule, name="Bob")
    carol = await _employee(client, department_id=sales, employee_type="contract", schedule_id=schedule, name="Carol")
    dave = await _employee(client, department_id=engineering, employee_type="intern", name="Dave")
    erin = await _employee(client, department_id=engineering, employee_type="permanent", name="Erin")
    frank = await _employee(client, department_id=engineering, employee_type="permanent", name="Frank")

    await _contract(client, alice["id"])
    await _contract(client, bob["id"])
    expiring_end = (date.today() + timedelta(days=10)).isoformat()
    await _contract(client, carol["id"], end_date=expiring_end)
    # dave: deliberately no contract at all -> "missing_contract".
    await _contract(client, erin["id"])
    await _contract(client, frank["id"])

    week_days = ["2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"]
    for day in week_days[:3]:
        await _attendance(client, alice["id"], day)
    for day in week_days:
        await _attendance(client, bob["id"], day)
    for day in week_days[:4]:
        await _attendance(client, carol["id"], day)
    await _attendance(client, carol["id"], week_days[4], absent=True)

    # Time off: one approved (alice), one pending (bob), one refused (carol).
    type_resp = await client.post(
        f"{BASE}/time-off-types/",
        json={"name": "Annual Leave", "code": f"dash-leave-{uuid.uuid4().hex[:8]}"},
        headers=HR_MANAGER,
    )
    assert type_resp.status_code == 201, type_resp.text
    leave_type = type_resp.json()

    alloc_resp = await client.post(
        f"{BASE}/time-off-allocations/",
        json={
            "employee_id": alice["id"],
            "time_off_type_id": leave_type["id"],
            "allocated": "10.00",
            "valid_from": "2026-01-01",
            "valid_to": "2026-12-31",
        },
        headers=HR_MANAGER,
    )
    assert alloc_resp.status_code == 201, alloc_resp.text

    alice_request = (
        await client.post(
            f"{BASE}/time-off-requests/",
            json={
                "employee_id": alice["id"],
                "time_off_type_id": leave_type["id"],
                "date_from": "2026-09-08",
                "date_to": "2026-09-09",
            },
            headers=HR_MANAGER,
        )
    ).json()
    approve = await client.post(
        f"{BASE}/time-off-requests/{alice_request['id']}/approve",
        json={"version": alice_request["version"]},
        headers=HR_MANAGER,
    )
    assert approve.status_code == 200, approve.text

    bob_request = await client.post(
        f"{BASE}/time-off-requests/",
        json={
            "employee_id": bob["id"],
            "time_off_type_id": leave_type["id"],
            "date_from": "2026-09-10",
            "date_to": "2026-09-10",
        },
        headers=HR_MANAGER,
    )
    assert bob_request.status_code == 201, bob_request.text

    carol_request = (
        await client.post(
            f"{BASE}/time-off-requests/",
            json={
                "employee_id": carol["id"],
                "time_off_type_id": leave_type["id"],
                "date_from": "2026-09-11",
                "date_to": "2026-09-11",
            },
            headers=HR_MANAGER,
        )
    ).json()
    refuse = await client.post(
        f"{BASE}/time-off-requests/{carol_request['id']}/refuse",
        json={"version": carol_request["version"]},
        headers=HR_MANAGER,
    )
    assert refuse.status_code == 200, refuse.text

    payrun_ids: list[int] = []
    payslip_ids: list[int] = []
    async with AsyncSessionLocal() as s:
        payrun_repo, payslip_repo = PayrunRepository(s), PayslipRepository(s)
        employee_repo = EmployeeRepository(s)

        structure = await SalaryStructureRepository(s).create(
            SalaryStructure(public_id="temp", name="Dashboard Test Structure", code=f"dash-{uuid.uuid4().hex[:8]}")
        )
        await s.flush()

        payrun = await payrun_repo.create(
            Payrun(
                public_id="temp",
                name="September 2026",
                salary_structure_id=structure.id,
                period_start=date(2026, 9, 1),
                period_end=date(2026, 9, 30),
                status=PayrunStatus.PAID,
            )
        )
        await s.flush()
        payrun_ids.append(payrun.id)

        async def _payslip(emp_public_id, *, net, status, warnings=None):
            employee_row = await employee_repo.get_by_public_id(emp_public_id)
            contract_row = (
                (
                    await s.execute(
                        select(Contract).where(Contract.employee_id == employee_row.id).limit(1)
                    )
                )
                .scalars()
                .first()
            )
            row = await payslip_repo.create(
                Payslip(
                    public_id="temp",
                    payrun_id=payrun.id,
                    employee_id=employee_row.id,
                    contract_id=contract_row.id,
                    worked_days=Decimal("20.00"),
                    gross_amount=net,
                    net_amount=net,
                    status=status,
                    warnings=warnings,
                )
            )
            await s.flush()
            payslip_ids.append(row.id)
            return row

        # REAL Phase 4-shaped entries — {code, severity, message, references},
        # exactly what `PayrollWarning.as_dict` writes
        # (app/services/payroll_context.py). Alice deliberately carries one
        # BLOCKING code the old normalizer happened to recognize
        # (missing_bank_details) and one it flattened to "other"
        # (missing_checkout), plus an ADVISORY, so a regression that loses
        # severity or re-flattens codes cannot pass by matching the easy one.
        await _payslip(
            alice["id"],
            net=Decimal("3000.00"),
            status=PayslipStatus.PAID,
            warnings=[
                {
                    "code": "missing_bank_details",
                    "severity": "blocking",
                    "message": "Alice Anderson has no bank account on file, so this payslip cannot be paid out.",
                    "references": [alice["id"]],
                },
                {
                    "code": "missing_checkout",
                    "severity": "blocking",
                    "message": "Alice Anderson has attendance without a check-out on 2026-09-09.",
                    "references": [alice["id"]],
                },
                {
                    "code": "structure_mismatch",
                    "severity": "advisory",
                    "message": "Contract names a different salary structure than this payrun.",
                    "references": [alice["id"]],
                },
            ],
        )
        await _payslip(
            bob["id"], net=Decimal("4000.00"), status=PayslipStatus.PAID, warnings=["Legacy note: reviewed manually"]
        )
        await _payslip(carol["id"], net=Decimal("2000.00"), status=PayslipStatus.PAID)
        await _payslip(erin["id"], net=Decimal("9999.00"), status=PayslipStatus.CANCELLED)
        await _payslip(frank["id"], net=Decimal("500.00"), status=PayslipStatus.VALIDATED)

        await s.commit()

    yield {
        "engineering": engineering,
        "sales": sales,
        "alice": alice,
        "bob": bob,
        "carol": carol,
        "dave": dave,
        "erin": erin,
        "frank": frank,
        #: Internal ids, so a test can exercise a state the API has no verb
        #: for from this side — soft-deleting the payrun out from under the
        #: dashboard, which Phase 4's own `delete_payrun` does.
        "payrun_ids": payrun_ids,
    }

    async with AsyncSessionLocal() as s:
        await s.execute(delete(Payslip).where(Payslip.id.in_(payslip_ids)))
        await s.execute(delete(Payrun).where(Payrun.id.in_(payrun_ids)))

        type_internal_id = decode_public_id(leave_type["id"], "tot")
        await s.execute(delete(TimeOffRequest).where(TimeOffRequest.time_off_type_id == type_internal_id))
        await s.execute(delete(TimeOffAllocation).where(TimeOffAllocation.time_off_type_id == type_internal_id))
        await s.execute(delete(TimeOffType).where(TimeOffType.id == type_internal_id))

        dept_ids = [decode_public_id(engineering, "dept"), decode_public_id(sales, "dept")]
        await s.execute(
            Employee.__table__.update().where(Employee.department_id.in_(dept_ids)).values(department_id=None)
        )
        await s.execute(
            Contract.__table__.update().where(Contract.department_id.in_(dept_ids)).values(department_id=None)
        )
        await s.execute(delete(Department).where(Department.id.in_(dept_ids)))
        await s.commit()


def _get(items, **match):
    for item in items:
        if all(item.get(k) == v for k, v in match.items()):
            return item
    return None


# =========================================================================== KPIs

async def test_total_net_salary_paid_sums_only_paid_payslips(client, dashboard_dataset):
    resp = await client.get(
        f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}",
        headers=PAYROLL_MANAGER,
    )
    assert resp.status_code == 200, resp.text
    kpis = resp.json()["kpis"]
    assert Decimal(kpis["total_net_salary_paid"]) == Decimal("9000.00")


async def test_payslips_generated_excludes_cancelled_but_includes_validated(client, dashboard_dataset):
    resp = await client.get(
        f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}",
        headers=PAYROLL_MANAGER,
    )
    assert resp.json()["kpis"]["payslips_generated"] == 4  # alice, bob, carol, frank — not erin (cancelled)


async def test_average_salary_is_per_employee_not_flat(client, dashboard_dataset):
    resp = await client.get(
        f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}",
        headers=PAYROLL_MANAGER,
    )
    assert Decimal(resp.json()["kpis"]["average_salary"]) == Decimal("3000.00")


async def test_approved_time_off_counts_only_approved_status(client, dashboard_dataset):
    """Scoped to this fixture's own department, deliberately.

    `approved_time_off` is an ORG-WIDE count when no department filter is
    given — which is the correct product behaviour, and exactly why an
    absolute assertion against the unfiltered number is not a valid test: it
    holds only on an empty database, and breaks the moment any unrelated row
    (another suite's fixture, a machine-local demo record) falls in the same
    window. Filtering by the fixture's own department makes the assertion
    about this dataset instead of about the whole table.
    """
    resp = await client.get(
        f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}"
        f"&department_id={dashboard_dataset['engineering']}",
        headers=PAYROLL_MANAGER,
    )
    assert resp.json()["kpis"]["approved_time_off"] == 1  # only alice's


async def test_attendance_health_is_schedule_derived_not_hardcoded(client, dashboard_dataset):
    resp = await client.get(
        f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}",
        headers=PAYROLL_MANAGER,
    )
    body = resp.json()
    assert body["attendance"]["expected_working_days"] == 15
    assert body["attendance"]["attended_days"] == 12
    assert Decimal(body["kpis"]["attendance_health_pct"]) == Decimal("80.00")


async def test_attendance_health_is_null_when_no_employee_has_a_schedule(client, dashboard_dataset):
    """dave/erin/frank have no schedule; scoping to employee_type=intern (dave
    alone) must not divide by zero."""
    resp = await client.get(
        f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}&employee_type=intern",
        headers=PAYROLL_MANAGER,
    )
    body = resp.json()
    assert body["attendance"]["expected_working_days"] == 0
    assert body["kpis"]["attendance_health_pct"] is None


# =========================================================================== charts

async def test_salary_cost_by_department(client, dashboard_dataset):
    resp = await client.get(
        f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}",
        headers=PAYROLL_MANAGER,
    )
    rows = resp.json()["salary_cost_by_department"]
    eng = _get(rows, department="Dashboard Engineering")
    sales = _get(rows, department="Dashboard Sales")
    assert Decimal(eng["amount"]) == Decimal("7000.00")
    assert Decimal(sales["amount"]) == Decimal("2000.00")


async def test_monthly_net_salary_trend(client, dashboard_dataset):
    resp = await client.get(
        f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}",
        headers=PAYROLL_MANAGER,
    )
    trend = resp.json()["monthly_net_salary_trend"]
    assert len(trend) == 1
    assert trend[0]["month"] == "2026-09"
    assert Decimal(trend[0]["amount"]) == Decimal("9000.00")


# =========================================================================== alerts

async def test_payroll_warnings_are_read_verbatim_and_normalized(client, dashboard_dataset):
    """Every field Phase 4 writes reaches the dashboard, with `code` verbatim.

    THIS IS THE REGRESSION TEST for the Phase 4/6 integration bug. Before the
    fix, `_normalize_warning` guessed at `category`/`type`/`code`, returned
    only `(category, message)`, and filtered the result against a set that
    predated Phase 4 — so `severity` and `references` were dropped entirely
    and four of Phase 4's six real codes (`missing_checkout`, `contract_gap`,
    `structure_mismatch`, `no_attendance`) were flattened to "other". A payrun
    blocked by a missing check-out looked exactly like one blocked by nothing
    in particular.

    The `code` assertion is the one that matters most: it is the join key
    someone uses to find this warning on the payslip it came from, so it must
    be Phase 4's own string and not a dashboard-local rename.
    """
    resp = await client.get(
        f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}",
        headers=PAYROLL_MANAGER,
    )
    warnings = resp.json()["warnings"]
    by_code = {w["code"]: w for w in warnings}

    # Alice's three Phase 4-shaped entries, plus Bob's unrecognizable one.
    assert len(warnings) == 4

    bank = by_code["missing_bank_details"]
    assert bank["severity"] == "blocking"
    assert bank["recognized"] is True
    assert bank["references"] == [dashboard_dataset["alice"]["id"]]
    assert "bank account" in bank["message"]
    assert bank["employee_id"] == dashboard_dataset["alice"]["id"]
    assert bank["payslip_id"].startswith("pslip_")

    # The code the OLD normalizer flattened to "other" — the exact regression.
    checkout = by_code["missing_checkout"]
    assert checkout["severity"] == "blocking"
    assert checkout["recognized"] is True

    # Severity is preserved, not assumed: this one is advisory.
    mismatch = by_code["structure_mismatch"]
    assert mismatch["severity"] == "advisory"
    assert mismatch["recognized"] is True

    # An entry with no recognizable shape is still reported — never dropped,
    # never relabelled as a real code — and is treated as blocking, because
    # "we could not read this payroll warning" is not a reassuring state.
    unrecognized = by_code["unrecognized"]
    assert unrecognized["recognized"] is False
    assert unrecognized["severity"] == "blocking"
    assert unrecognized["message"] == "Legacy note: reviewed manually"


async def test_blocking_warnings_sort_above_advisory_ones(client, dashboard_dataset):
    """PRD §5.10's gate is about what stops a payrun being finalized, so an
    advisory must not push a blocking finding down the list."""
    resp = await client.get(
        f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}",
        headers=PAYROLL_MANAGER,
    )
    severities = [w["severity"] for w in resp.json()["warnings"]]
    assert severities == sorted(severities, key=lambda s: s != "blocking")
    assert severities[-1] == "advisory"


@pytest.mark.parametrize(
    "code, severity",
    [
        ("missing_bank_details", "blocking"),
        ("missing_checkout", "blocking"),
        ("contract_gap", "blocking"),
        ("duplicate_payslip", "blocking"),
        ("no_payslip", "blocking"),
        ("structure_mismatch", "advisory"),
        ("no_attendance", "advisory"),
    ],
)
def test_every_real_phase4_warning_code_is_recognized(code, severity):
    """Every code Phase 4 can emit survives normalization with its own code
    and severity intact.

    Parametrized over the full set read off `warning_checks`
    (app/services/payroll_context.py) and `validation_report`
    (app/services/payroll.py). If Phase 4 adds a code and nobody teaches this
    dashboard about it, the new code still renders — `recognized` goes False
    rather than the entry being relabelled — but this list is the reminder to
    add it deliberately.
    """
    from app.services.dashboard import _normalize_warning

    normalized = _normalize_warning(
        {"code": code, "severity": severity, "message": "m", "references": ["emp_x"]}
    )
    assert normalized["code"] == code, "the code must pass through verbatim, never renamed"
    assert normalized["severity"] == severity
    assert normalized["references"] == ["emp_x"]
    assert normalized["recognized"] is True


def test_an_unknown_future_code_is_reported_not_relabelled():
    """A warning a later phase adds must stay visible under its own name."""
    from app.services.dashboard import _normalize_warning

    normalized = _normalize_warning(
        {"code": "some_future_check", "severity": "advisory", "message": "m", "references": []}
    )
    assert normalized["code"] == "some_future_check"
    assert normalized["severity"] == "advisory"
    assert normalized["recognized"] is False


async def test_payslips_of_a_soft_deleted_payrun_are_excluded(client, dashboard_dataset, session):
    """Phase 4's `delete_payrun` SOFT-deletes the run and leaves its payslips
    in place, so the dashboard's payrun join must exclude deleted runs.

    Without `Payrun.deleted_at IS NULL` in `_period_overlaps_payrun`, a
    deleted draft or computed payrun's payslips kept appearing in
    `payslips_generated` and in the warning feed.
    """
    from datetime import UTC, datetime

    from app.models.payroll import Payrun as PayrunModel

    before = (
        await client.get(
            f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}",
            headers=PAYROLL_MANAGER,
        )
    ).json()
    assert before["kpis"]["payslips_generated"] > 0
    assert before["warnings"]

    payrun_id = dashboard_dataset["payrun_ids"][0]
    async with AsyncSessionLocal() as s:
        await s.execute(
            PayrunModel.__table__.update()
            .where(PayrunModel.id == payrun_id)
            .values(deleted_at=datetime.now(UTC))
        )
        await s.commit()
    try:
        after = (
            await client.get(
                f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}",
                headers=PAYROLL_MANAGER,
            )
        ).json()
        assert after["kpis"]["payslips_generated"] == 0
        assert after["warnings"] == []
        assert Decimal(after["kpis"]["total_net_salary_paid"]) == Decimal("0.00")
    finally:
        async with AsyncSessionLocal() as s:
            await s.execute(
                PayrunModel.__table__.update()
                .where(PayrunModel.id == payrun_id)
                .values(deleted_at=None)
            )
            await s.commit()


async def test_contract_attention_flags_expiring_and_missing(client, dashboard_dataset):
    resp = await client.get(
        f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}",
        headers=PAYROLL_MANAGER,
    )
    items = resp.json()["contract_attention"]
    expiring = _get(items, kind="expiring", employee_id=dashboard_dataset["carol"]["id"])
    missing = _get(items, kind="missing_contract", employee_id=dashboard_dataset["dave"]["id"])
    assert expiring is not None
    assert missing is not None


# =========================================================================== overviews

async def test_attendance_by_status_breakdown(client, dashboard_dataset):
    resp = await client.get(
        f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}",
        headers=PAYROLL_MANAGER,
    )
    by_status = {row["status"]: row["count"] for row in resp.json()["attendance"]["by_status"]}
    assert by_status["present"] == 12
    assert by_status["absent"] == 1


async def test_time_off_overview_breakdown_and_balance(client, dashboard_dataset):
    """Per-department, so the counts are about this fixture rather than about
    every row in the table — see the note on
    `test_approved_time_off_counts_only_approved_status`. Engineering holds
    alice (approved) and bob (pending); Sales holds carol (refused)."""
    engineering = (
        await client.get(
            f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}"
            f"&department_id={dashboard_dataset['engineering']}",
            headers=PAYROLL_MANAGER,
        )
    ).json()["time_off"]
    assert engineering["pending"] == 1  # bob
    assert engineering["approved"] == 1  # alice
    assert engineering["refused"] == 0

    sales = (
        await client.get(
            f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}"
            f"&department_id={dashboard_dataset['sales']}",
            headers=PAYROLL_MANAGER,
        )
    ).json()["time_off"]
    assert sales["refused"] == 1  # carol

    time_off = engineering
    # The balance summary is keyed by type name, so this fixture's own type is
    # already isolated from anything else in the table.
    balance = _get(time_off["balance_summary"], time_off_type="Annual Leave")
    assert Decimal(balance["allocated"]) == Decimal("10.00")
    assert Decimal(balance["taken"]) == Decimal("2.00")
    assert Decimal(balance["remaining"]) == Decimal("8.00")


async def test_department_breakdown_headcount_and_spend(client, dashboard_dataset):
    resp = await client.get(
        f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}",
        headers=PAYROLL_MANAGER,
    )
    rows = resp.json()["department_breakdown"]
    eng = _get(rows, department="Dashboard Engineering")
    sales = _get(rows, department="Dashboard Sales")
    assert eng["headcount"] == 5  # alice, bob, dave, erin, frank
    assert Decimal(eng["payroll_spend"]) == Decimal("7000.00")
    assert sales["headcount"] == 1
    assert Decimal(sales["payroll_spend"]) == Decimal("2000.00")


# =========================================================================== filters

async def test_period_filter_excludes_payruns_outside_the_window(client, dashboard_dataset):
    resp = await client.get(
        f"{BASE}/dashboard/summary?period_start=2025-01-01&period_end=2025-01-31",
        headers=PAYROLL_MANAGER,
    )
    kpis = resp.json()["kpis"]
    assert Decimal(kpis["total_net_salary_paid"]) == Decimal("0.00")
    assert kpis["payslips_generated"] == 0


async def test_department_filter_narrows_every_widget_consistently(client, dashboard_dataset):
    resp = await client.get(
        f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}"
        f"&department_id={dashboard_dataset['engineering']}",
        headers=PAYROLL_MANAGER,
    )
    body = resp.json()
    assert Decimal(body["kpis"]["total_net_salary_paid"]) == Decimal("7000.00")
    assert body["kpis"]["payslips_generated"] == 3  # alice, bob, frank (erin cancelled)
    assert Decimal(body["kpis"]["average_salary"]) == Decimal("3500.00")
    assert len(body["salary_cost_by_department"]) == 1
    assert body["salary_cost_by_department"][0]["department"] == "Dashboard Engineering"
    assert len(body["department_breakdown"]) == 1


async def test_employee_type_filter_narrows_every_widget_consistently(client, dashboard_dataset):
    """Both filters together, so the assertions are about this fixture.

    The money figure is already isolated (only this fixture's payslips sit on
    a payrun in this window); the time-off count is not, so it is scoped by
    department as well — see
    `test_approved_time_off_counts_only_approved_status`.
    """
    resp = await client.get(
        f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}&employee_type=permanent",
        headers=PAYROLL_MANAGER,
    )
    body = resp.json()
    # permanent: alice, bob, erin, frank — carol (contract) and dave (intern) excluded.
    assert Decimal(body["kpis"]["total_net_salary_paid"]) == Decimal("7000.00")

    scoped = (
        await client.get(
            f"{BASE}/dashboard/summary?period_start={PERIOD_START}&period_end={PERIOD_END}"
            f"&employee_type=permanent&department_id={dashboard_dataset['engineering']}",
            headers=PAYROLL_MANAGER,
        )
    ).json()
    assert scoped["kpis"]["approved_time_off"] == 1  # alice's, still permanent
    # The type filter genuinely narrows: carol is contract, so Sales' paid
    # payslip drops out of the permanent-only money total.
    assert Decimal(scoped["kpis"]["total_net_salary_paid"]) == Decimal("7000.00")


# =========================================================================== RBAC

async def test_employee_and_hr_manager_are_denied_the_dashboard(client, dashboard_dataset):
    assert (await client.get(f"{BASE}/dashboard/summary", headers=EMPLOYEE)).status_code == 403
    assert (await client.get(f"{BASE}/dashboard/summary", headers=HR_MANAGER)).status_code == 403


@pytest.mark.parametrize("headers", [PAYROLL_USER, PAYROLL_MANAGER, ADMIN])
async def test_payroll_roles_and_admin_can_read_the_dashboard(client, dashboard_dataset, headers):
    resp = await client.get(f"{BASE}/dashboard/summary", headers=headers)
    assert resp.status_code == 200, resp.text


async def test_bad_period_range_is_a_400(client):
    resp = await client.get(
        f"{BASE}/dashboard/summary?period_start=2026-09-30&period_end=2026-09-01",
        headers=PAYROLL_MANAGER,
    )
    assert resp.status_code == 400
