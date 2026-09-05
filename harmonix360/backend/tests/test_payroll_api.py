"""Payrun / Payslip (PS B5/B6/B7, Architecture §6/§7).

The test that matters most in this phase is
`test_golden_payslip_is_hand_verifiable_end_to_end`: a payslip computed
through the REAL HTTP path — real employee, real contract, real attendance,
real approved leave, real salary structure, the real Phase 3 resolver — and
asserted against figures worked out on paper, with exact `Decimal` equality
and no `pytest.approx` anywhere. Phase 3 proved the resolver reproduces a
hand-computed structure; this proves the whole pipeline around it delivers
those same figures onto a persisted payslip.

Everything else here defends a specific way payroll goes wrong: computing
twice, retrying a request, two people pressing Compute at once, a contract
that does not cover the period, no contract at all, and a finalized run
someone tries to change.
"""
import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import decode_public_id as _decode
from app.middleware.idempotency import cache_key
from app.models.enums import (
    PayrunStatus,
    PayslipStatus,
    SalaryRuleCategory,
    SalaryRuleComputation,
    UserRole,
)
from app.models.payroll import Payslip
from app.services.payroll_context import period_days
from app.services.salary_resolver import SEED_CONTEXT_NAMES
from tests.conftest import auth_headers, unique_email

HR_MANAGER = auth_headers(UserRole.HR_MANAGER)
EMPLOYEE = auth_headers(UserRole.EMPLOYEE)
PAYROLL_USER = auth_headers(UserRole.HR_PAYROLL_USER)
PAYROLL_MANAGER = auth_headers(UserRole.HR_PAYROLL_MANAGER)
ADMIN = auth_headers(UserRole.ADMIN)

#: A period entirely in the past, so no attendance row in it can still be
#: "open today" — `derive_attendance_status` turns an un-checked-out entry
#: into missing_checkout only once its day has ended, and a period that
#: includes today would make the golden test's status depend on the clock.
PERIOD_START = date(2025, 3, 1)
PERIOD_END = date(2025, 3, 31)


def idem() -> dict[str, str]:
    """A fresh `Idempotency-Key` header. Required on payrun create and compute
    (Architecture §6), and fresh per call so a test never accidentally replays
    a previous test's cached response."""
    return {"Idempotency-Key": f"test-{uuid.uuid4().hex}"}


def headers(role_headers: dict, *, with_key: bool = False) -> dict:
    return {**role_headers, **(idem() if with_key else {})}


# ===========================================================================
# Builders — real rows through the real endpoints
# ===========================================================================


async def make_employee(client, *, bank_account: str | None = "GB00TEST00001", **overrides) -> dict:
    body = {
        "first_name": "Golden",
        "last_name": "Payslip",
        "work_email": unique_email("payroll"),
        "bank_account": bank_account,
        **overrides,
    }
    response = await client.post("/api/v1/employees/", json=body, headers=HR_MANAGER)
    assert response.status_code == 201, response.text
    return response.json()


async def make_contract(client, employee_id: str, *, wage: str, **overrides) -> dict:
    body = {
        "employee_id": employee_id,
        "wage": wage,
        "start_date": str(PERIOD_START),
        "end_date": None,
        "status": "active",
        **overrides,
    }
    response = await client.post("/api/v1/contracts/", json=body, headers=HR_MANAGER)
    assert response.status_code == 201, response.text
    return response.json()


async def attach_working_schedule(client, employee_id: str) -> dict:
    """Explicit Mon-Fri schedule for tests asserting a finalizable run.

    LOP's denominator is required now. Existing golden tests deliberately keep
    their original inputs; only clean/finalization fixtures call this helper.
    cleanup_payroll depends on cleanup_schedules to remove these rows.
    """
    response = await client.post("/api/v1/working-schedules/", headers=HR_MANAGER, json={
        "name": "Payroll test weekdays", "lines": [
            {"day_of_week": day, "start_time": "09:00", "end_time": "17:00", "break_minutes": 0}
            for day in ("monday", "tuesday", "wednesday", "thursday", "friday")
        ],
    })
    assert response.status_code == 201, response.text
    schedule = response.json()
    response = await client.patch(f"/api/v1/employees/{employee_id}", headers=HR_MANAGER,
        json={"default_schedule_id": schedule["id"]})
    assert response.status_code == 200, response.text
    return schedule


async def make_rule(client, code: str, method: SalaryRuleComputation, category: SalaryRuleCategory, **fields):
    body = {
        "name": code.replace("_", " ").title(),
        # Unique per tenant: the suite runs repeatedly against one database and
        # `uq_salary_rule_code_tenant` does not care that a previous run's rule
        # was only a fixture.
        "code": f"{code}_{uuid.uuid4().hex[:6].upper()}",
        "category": category.value,
        "computation_method": method.value,
        **fields,
    }
    response = await client.post("/api/v1/salary-rules/", json=body, headers=PAYROLL_MANAGER)
    assert response.status_code == 201, response.text
    return response.json()


async def make_structure(client, rules: list[tuple[dict, int]]) -> dict:
    body = {
        "name": "Golden Structure",
        "code": f"GOLD_{uuid.uuid4().hex[:6].upper()}",
        "rules": [{"salary_rule_id": rule["id"], "sequence": sequence} for rule, sequence in rules],
    }
    response = await client.post("/api/v1/salary-structures/", json=body, headers=PAYROLL_MANAGER)
    assert response.status_code == 201, response.text
    return response.json()


async def make_attendance(client, employee_id: str, day: date, *, hours: int = 8, check_out: bool = True):
    check_in = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=9)
    body = {
        "employee_id": employee_id,
        "check_in": check_in.isoformat(),
        "check_out": (check_in + timedelta(hours=hours)).isoformat() if check_out else None,
    }
    response = await client.post("/api/v1/attendance/", json=body, headers=HR_MANAGER)
    assert response.status_code == 201, response.text
    return response.json()


async def make_payroll_leave(client, employee_id: str, date_from: date, date_to: date) -> dict:
    """An APPROVED absence of a payroll-integrated type — the only kind that
    reaches UNPAID_LEAVE_DAYS.

    `requires_allocation=False` so approval needs no allocation and no
    balance: this fixture is about what payroll sees, and Phase 2 already
    proves the allocation debit separately.
    """
    type_response = await client.post(
        "/api/v1/time-off-types/",
        json={
            "name": "Unpaid Leave",
            "code": f"UNPAID_{uuid.uuid4().hex[:6].upper()}",
            "unit": "days",
            "requires_allocation": False,
            "requires_approval": True,
            "payroll_integration": True,
        },
        headers=HR_MANAGER,
    )
    assert type_response.status_code == 201, type_response.text

    request_response = await client.post(
        "/api/v1/time-off-requests/",
        json={
            "employee_id": employee_id,
            "time_off_type_id": type_response.json()["id"],
            "date_from": str(date_from),
            "date_to": str(date_to),
            "reason": "Golden payslip fixture",
        },
        headers=HR_MANAGER,
    )
    assert request_response.status_code == 201, request_response.text
    request = request_response.json()

    approved = await client.post(
        f"/api/v1/time-off-requests/{request['id']}/approve",
        json={"version": request["version"], "decision_note": "ok"},
        headers=HR_MANAGER,
    )
    assert approved.status_code == 200, approved.text
    return approved.json()


async def make_payrun(client, structure_id: str, employee_ids: list[str], **overrides) -> dict:
    body = {
        "name": "March 2025",
        "salary_structure_id": structure_id,
        "period_start": str(PERIOD_START),
        "period_end": str(PERIOD_END),
        "employee_ids": employee_ids,
        **overrides,
    }
    response = await client.post("/api/v1/payruns/", json=body, headers=headers(PAYROLL_USER, with_key=True))
    assert response.status_code == 201, response.text
    return response.json()


async def compute(client, payrun: dict, *, expect: int = 200, key: dict | None = None):
    response = await client.post(
        f"/api/v1/payruns/{payrun['id']}/compute",
        json={"version": payrun["version"]},
        headers={**PAYROLL_USER, **(key or idem())},
    )
    assert response.status_code == expect, response.text
    return response


async def standard_structure(client) -> dict:
    """Basic 30000 → HRA 40% of Basic → Gross (Basic+HRA) → PT 200 → Net.

    The same shape as Phase 3's hand-computed acceptance test, deliberately:
    if this phase's pipeline produces different numbers from the same rules,
    the difference is in the pipeline, not in the arithmetic.
    """
    basic = await make_rule(
        client, "BASIC", SalaryRuleComputation.FIXED, SalaryRuleCategory.BASIC, amount="30000.00"
    )
    hra = await make_rule(
        client,
        "HRA",
        SalaryRuleComputation.PERCENTAGE,
        SalaryRuleCategory.ALLOWANCE,
        amount="40.00",
        percentage_base_code=basic["code"],
    )
    gross = await make_rule(
        client,
        "GROSS",
        SalaryRuleComputation.FORMULA,
        SalaryRuleCategory.GROSS,
        expression=f"{basic['code']} + {hra['code']}",
    )
    tax = await make_rule(
        client, "PT", SalaryRuleComputation.FIXED, SalaryRuleCategory.DEDUCTION, amount="200.00"
    )
    net = await make_rule(
        client,
        "NET",
        SalaryRuleComputation.FORMULA,
        SalaryRuleCategory.NET,
        expression=f"{gross['code']} - {tax['code']}",
    )
    structure = await make_structure(
        client, [(basic, 10), (hra, 20), (gross, 30), (tax, 40), (net, 50)]
    )
    structure["_codes"] = {
        "basic": basic["code"],
        "hra": hra["code"],
        "gross": gross["code"],
        "tax": tax["code"],
        "net": net["code"],
    }
    return structure


# ===========================================================================
# THE GOLDEN PAYSLIP — hand-computed, exact Decimal equality
# ===========================================================================


async def test_golden_payslip_is_hand_verifiable_end_to_end(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """One payslip, computed through the real API, checked against arithmetic
    done on paper.

    THE PAPER WORKING
    -----------------
    Employee: one active contract, wage 45000.00, covering the whole of March
    2025. Attendance: check-in and check-out on 3, 4 and 5 March — three
    counted worked days. Approved unpaid leave (a payroll-integrated type):
    10-12 March inclusive — three unpaid leave days.

    Context (Architecture §7 step 3):
        WORKED_DAYS        = 3
        CONTRACT_WAGE      = 45000.00
        UNPAID_LEAVE_DAYS  = 3

    Structure, in sequence (step 4):
        BASIC   fixed                   = 30000.00
        HRA     40% of BASIC            = 30000.00 * 40 / 100      = 12000.00
        GROSS   formula BASIC + HRA     = 30000.00 + 12000.00      = 42000.00
        PT      fixed deduction         =   200.00
        NET     formula GROSS - PT      = 42000.00 -   200.00      = 41800.00

    Totals (step 5): the structure declares GROSS and NET, so the payslip's
    gross is 42000.00 and its net is 41800.00 — not a re-derivation from the
    categories.

    Every assertion below compares `Decimal` to `Decimal`. A cent is not
    "close enough" on a payslip, so there is no tolerance anywhere in this
    test.
    """
    employee = await make_employee(client)
    await make_contract(client, employee["id"], wage="45000.00")

    for day in (date(2025, 3, 3), date(2025, 3, 4), date(2025, 3, 5)):
        await make_attendance(client, employee["id"], day)
    await make_payroll_leave(client, employee["id"], date(2025, 3, 10), date(2025, 3, 12))

    structure = await standard_structure(client)
    payrun = await make_payrun(client, structure["id"], [employee["id"]])

    computed = (await compute(client, payrun)).json()
    assert computed["computed_count"] == 1
    assert computed["skipped"] == []
    assert computed["payrun"]["status"] == PayrunStatus.COMPUTED.value

    payslips = (
        await client.get(f"/api/v1/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()["items"]
    assert len(payslips) == 1
    payslip = payslips[0]

    # --- the context that fed the rules ---------------------------------
    assert Decimal(payslip["worked_days"]) == Decimal("3.00")

    # --- every line, in sequence, to the cent ----------------------------
    codes = structure["_codes"]
    expected_lines = [
        (codes["basic"], SalaryRuleCategory.BASIC.value, Decimal("30000.00")),
        (codes["hra"], SalaryRuleCategory.ALLOWANCE.value, Decimal("12000.00")),
        (codes["gross"], SalaryRuleCategory.GROSS.value, Decimal("42000.00")),
        (codes["tax"], SalaryRuleCategory.DEDUCTION.value, Decimal("200.00")),
        (codes["net"], SalaryRuleCategory.NET.value, Decimal("41800.00")),
    ]
    actual_lines = [
        (line["code"], line["category"], Decimal(line["amount"])) for line in payslip["lines"]
    ]
    assert actual_lines == expected_lines

    # --- the denormalised totals -----------------------------------------
    assert Decimal(payslip["gross_amount"]) == Decimal("42000.00")
    assert Decimal(payslip["net_amount"]) == Decimal("41800.00")

    # --- and the payslip is tied to the contract that produced it --------
    assert Decimal(payslip["contract"]["wage"]) == Decimal("45000.00")
    assert payslip["employee"]["id"] == employee["id"]
    assert payslip["status"] == PayslipStatus.COMPUTED.value


async def test_golden_payslip_context_reaches_the_rules_that_reference_it(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """The seed context is not decorative — a formula that references it gets
    the real attendance and leave numbers.

    Paper working: wage 30000.00 over a 31-day period, 4 attended days, 2
    unpaid leave days.

        PER_DAY  = CONTRACT_WAGE / 30              = 30000.00 / 30 = 1000.00
        EARNED   = PER_DAY * WORKED_DAYS           = 1000.00 * 4   = 4000.00
        DOCKED   = PER_DAY * UNPAID_LEAVE_DAYS     = 1000.00 * 2   = 2000.00
        NET      = EARNED - DOCKED                 = 4000.00 - 2000.00 = 2000.00
    """
    employee = await make_employee(client)
    await make_contract(client, employee["id"], wage="30000.00")
    for day in (date(2025, 3, 3), date(2025, 3, 4), date(2025, 3, 5), date(2025, 3, 6)):
        await make_attendance(client, employee["id"], day)
    await make_payroll_leave(client, employee["id"], date(2025, 3, 17), date(2025, 3, 18))

    per_day = await make_rule(
        client,
        "PER_DAY",
        SalaryRuleComputation.FORMULA,
        SalaryRuleCategory.BASIC,
        expression="CONTRACT_WAGE / 30",
    )
    earned = await make_rule(
        client,
        "EARNED",
        SalaryRuleComputation.FORMULA,
        SalaryRuleCategory.GROSS,
        expression=f"{per_day['code']} * WORKED_DAYS",
    )
    docked = await make_rule(
        client,
        "DOCKED",
        SalaryRuleComputation.FORMULA,
        SalaryRuleCategory.DEDUCTION,
        expression=f"{per_day['code']} * UNPAID_LEAVE_DAYS",
    )
    net = await make_rule(
        client,
        "NETPAY",
        SalaryRuleComputation.FORMULA,
        SalaryRuleCategory.NET,
        expression=f"{earned['code']} - {docked['code']}",
    )
    structure = await make_structure(
        client, [(per_day, 10), (earned, 20), (docked, 30), (net, 40)]
    )
    payrun = await make_payrun(client, structure["id"], [employee["id"]])
    await compute(client, payrun)

    payslip = (
        await client.get(f"/api/v1/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()["items"][0]
    amounts = {line["code"]: Decimal(line["amount"]) for line in payslip["lines"]}

    assert amounts[per_day["code"]] == Decimal("1000.00")
    assert amounts[earned["code"]] == Decimal("4000.00")
    assert amounts[docked["code"]] == Decimal("2000.00")
    assert amounts[net["code"]] == Decimal("2000.00")
    assert Decimal(payslip["net_amount"]) == Decimal("2000.00")
    assert Decimal(payslip["worked_days"]) == Decimal("4.00")


def test_the_context_this_phase_builds_uses_exactly_phase_3s_vocabulary():
    """A guard on the seam between the phases.

    `SEED_CONTEXT_NAMES` is Phase 3's fixed vocabulary and the reason a
    structure's formulas can be validated at SAVE time, before any payrun
    exists. If Phase 4 ever passes a name outside it, structures referencing
    that name would pass validation at save time and fail at run time — the
    exact failure mode Phase 3 designed the constant to make impossible.
    """
    assert SEED_CONTEXT_NAMES == {"WORKED_DAYS", "CONTRACT_WAGE", "UNPAID_LEAVE_DAYS", "LOP_AMOUNT"}
    # The new LOP golden test checks the actual built context and Decimal
    # values; unavailable LOP is explicitly omitted with a blocking warning.


# ===========================================================================
# Duplicate compute, replayed keys, concurrency
# ===========================================================================


async def test_recompute_replaces_payslips_instead_of_duplicating_them(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """Computing twice leaves ONE payslip per employee, with the second run's
    figures.

    The employee gains a worked day between the two computes, so the test can
    tell "recomputed" from "left alone" — a recompute that quietly kept the
    first result would pass a count-only assertion.
    """
    employee = await make_employee(client)
    await make_contract(client, employee["id"], wage="30000.00")
    await make_attendance(client, employee["id"], date(2025, 3, 3))

    worked = await make_rule(
        client,
        "WORKED",
        SalaryRuleComputation.FORMULA,
        SalaryRuleCategory.NET,
        expression="WORKED_DAYS * 100",
    )
    structure = await make_structure(client, [(worked, 10)])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])

    first = (await compute(client, payrun)).json()
    assert first["computed_count"] == 1
    first_payslips = (
        await client.get(f"/api/v1/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()
    assert first_payslips["total"] == 1
    assert Decimal(first_payslips["items"][0]["net_amount"]) == Decimal("100.00")

    await make_attendance(client, employee["id"], date(2025, 3, 4))

    # A recompute bumps the payrun's version, so the second call must send the
    # version the first one produced — a stale version is a 409 by design.
    refreshed = (await client.get(f"/api/v1/payruns/{payrun['id']}", headers=PAYROLL_USER)).json()
    assert refreshed["version"] > payrun["version"]
    second = (await compute(client, refreshed)).json()
    assert second["computed_count"] == 1

    after = (
        await client.get(f"/api/v1/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()
    assert after["total"] == 1, "a recompute must replace payslips, never add a second set"
    assert Decimal(after["items"][0]["net_amount"]) == Decimal("200.00")


async def test_compute_with_a_stale_version_is_refused(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """The second of two sequential computes sent with the SAME version — a
    double-submitted form with no idempotency key — is a 409, not a silent
    second run."""
    employee = await make_employee(client)
    await make_contract(client, employee["id"], wage="30000.00")
    rule = await make_rule(
        client, "FLAT", SalaryRuleComputation.FIXED, SalaryRuleCategory.NET, amount="500.00"
    )
    structure = await make_structure(client, [(rule, 10)])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])

    await compute(client, payrun)
    stale = await compute(client, payrun, expect=409)
    assert "version" in stale.text.lower()


async def test_a_replayed_idempotency_key_returns_the_first_answer_and_runs_once(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """Architecture §6's duplicate-payslip defence, at the middleware.

    The same key sent twice must return the FIRST response verbatim without
    the engine running again. Proved by changing the underlying data between
    the two calls: a second real compute would pick the new attendance up, so
    an identical response body is evidence that nothing ran.
    """
    employee = await make_employee(client)
    await make_contract(client, employee["id"], wage="30000.00")
    await make_attendance(client, employee["id"], date(2025, 3, 3))
    rule = await make_rule(
        client,
        "DAYS",
        SalaryRuleComputation.FORMULA,
        SalaryRuleCategory.NET,
        expression="WORKED_DAYS * 100",
    )
    structure = await make_structure(client, [(rule, 10)])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])

    key = idem()
    first = (await compute(client, payrun, key=key)).json()

    await make_attendance(client, employee["id"], date(2025, 3, 4))
    replay = (await compute(client, payrun, key=key)).json()

    assert replay == first, "a replayed Idempotency-Key must return the cached response verbatim"

    payslips = (
        await client.get(f"/api/v1/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()
    assert payslips["total"] == 1
    assert Decimal(payslips["items"][0]["net_amount"]) == Decimal("100.00"), (
        "the replay must not have recomputed — the day added between the calls must be absent"
    )

    from app.core.redis import redis_client

    await redis_client.delete(
        cache_key("POST", f"/api/v1/payruns/{payrun['id']}/compute", key["Idempotency-Key"])
    )


async def test_idempotency_key_is_required_on_create_and_compute(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """Architecture §6 says REQUIRED, so a request without the header is
    refused rather than quietly accepted."""
    employee = await make_employee(client)
    await make_contract(client, employee["id"], wage="30000.00")
    rule = await make_rule(
        client, "FLAT", SalaryRuleComputation.FIXED, SalaryRuleCategory.NET, amount="500.00"
    )
    structure = await make_structure(client, [(rule, 10)])

    keyless_create = await client.post(
        "/api/v1/payruns/",
        json={
            "name": "No key",
            "salary_structure_id": structure["id"],
            "period_start": str(PERIOD_START),
            "period_end": str(PERIOD_END),
            "employee_ids": [employee["id"]],
        },
        headers=PAYROLL_USER,
    )
    assert keyless_create.status_code == 400
    assert "idempotency-key" in keyless_create.text.lower()

    payrun = await make_payrun(client, structure["id"], [employee["id"]])
    keyless_compute = await client.post(
        f"/api/v1/payruns/{payrun['id']}/compute",
        json={"version": payrun["version"]},
        headers=PAYROLL_USER,
    )
    assert keyless_compute.status_code == 400


async def test_concurrent_computes_of_one_payrun_do_not_interleave(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """Two Computes of the same payrun, fired together with DIFFERENT
    idempotency keys (so the middleware cannot collapse them into one).

    `acquire_entity_lock(session, "payrun", id)` serialises them: the loser
    waits, then re-reads the payrun the winner committed and finds its own
    version stale, so exactly one succeeds. The property that matters is the
    one at the end — one payslip per employee, from one run of the engine —
    because the failure this lock exists to prevent is a payslip set that
    neither compute intended, with every individual row still perfectly valid.
    """
    employee = await make_employee(client)
    await make_contract(client, employee["id"], wage="30000.00")
    rule = await make_rule(
        client, "FLAT", SalaryRuleComputation.FIXED, SalaryRuleCategory.NET, amount="500.00"
    )
    structure = await make_structure(client, [(rule, 10)])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])

    async def fire():
        return await client.post(
            f"/api/v1/payruns/{payrun['id']}/compute",
            json={"version": payrun["version"]},
            headers={**PAYROLL_USER, **idem()},
        )

    first, second = await asyncio.gather(fire(), fire())
    statuses = sorted([first.status_code, second.status_code])
    assert statuses == [200, 409], f"expected exactly one winner, got {statuses}"

    payslips = (
        await client.get(f"/api/v1/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()
    assert payslips["total"] == 1

    # Read past the API, at the rows themselves: the interleaving this lock
    # prevents would leave a payslip set that the API's own filters could
    # still render as tidy.
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(Payslip.id, Payslip.employee_id).where(
                    Payslip.payrun_id == _decode(payrun["id"], "prun")
                )
            )
        ).all()
    assert len(rows) == 1, f"expected exactly one payslip row, found {len(rows)}"


# ===========================================================================
# Contract resolution
# ===========================================================================


async def test_an_employee_with_no_applicable_contract_is_skipped_not_guessed(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """Architecture §7 step 1 cannot be satisfied, so no payslip is invented.

    The employee is reported in `skipped` with a reason, the rest of the run
    still computes, and Validate later refuses to finalize a run whose
    selection is not fully covered.
    """
    paid = await make_employee(client)
    await make_contract(client, paid["id"], wage="30000.00")
    contractless = await make_employee(client)

    rule = await make_rule(
        client, "FLAT", SalaryRuleComputation.FIXED, SalaryRuleCategory.NET, amount="500.00"
    )
    structure = await make_structure(client, [(rule, 10)])
    payrun = await make_payrun(client, structure["id"], [paid["id"], contractless["id"]])

    result = (await compute(client, payrun)).json()
    assert result["computed_count"] == 1
    assert len(result["skipped"]) == 1
    skipped = result["skipped"][0]
    assert skipped["employee_id"] == contractless["id"]
    assert "no active contract" in skipped["reason"]

    report = (
        await client.get(f"/api/v1/payruns/{payrun['id']}/validation", headers=PAYROLL_USER)
    ).json()
    assert report["blocking_by_code"]["no_payslip"] == 1

    refreshed = (await client.get(f"/api/v1/payruns/{payrun['id']}", headers=PAYROLL_USER)).json()
    blocked = await client.post(
        f"/api/v1/payruns/{payrun['id']}/validate",
        json={"version": refreshed["version"]},
        headers=PAYROLL_USER,
    )
    assert blocked.status_code == 409
    assert "no_payslip" in blocked.text


async def test_a_draft_contract_does_not_make_an_employee_payable(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """Only `status='active'` contracts resolve — the same predicate the
    non-overlap EXCLUDE constraint uses. A draft contract is a proposal, and
    paying somebody from a proposal is exactly the mistake the predicate
    exists to prevent."""
    employee = await make_employee(client)
    await make_contract(client, employee["id"], wage="30000.00", status="draft")

    rule = await make_rule(
        client, "FLAT", SalaryRuleComputation.FIXED, SalaryRuleCategory.NET, amount="500.00"
    )
    structure = await make_structure(client, [(rule, 10)])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])

    result = (await compute(client, payrun)).json()
    assert result["computed_count"] == 0
    assert len(result["skipped"]) == 1


async def test_overlapping_active_contracts_are_impossible_so_resolution_stays_unique(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """The "two contracts for one period" scenario, from payroll's side.

    Payroll's uniqueness guarantee is borrowed entirely from
    `contracts_active_period_overlap_excl`, so the meaningful test is that the
    database refuses to create the ambiguity in the first place — and that a
    payrun over the period still resolves exactly one contract afterwards.
    """
    employee = await make_employee(client)
    first = await make_contract(
        client, employee["id"], wage="30000.00", start_date="2025-01-01", end_date="2025-06-30"
    )

    conflicting = await client.post(
        "/api/v1/contracts/",
        json={
            "employee_id": employee["id"],
            "wage": "99000.00",
            # Overlaps `first` at both ends of March; '[]' bounds make even a
            # single shared day a conflict.
            "start_date": "2025-03-15",
            "end_date": "2025-09-30",
            "status": "active",
        },
        headers=HR_MANAGER,
    )
    assert conflicting.status_code == 409, conflicting.text
    assert "active contract" in conflicting.text

    rule = await make_rule(
        client, "WAGE", SalaryRuleComputation.FORMULA, SalaryRuleCategory.NET, expression="CONTRACT_WAGE"
    )
    structure = await make_structure(client, [(rule, 10)])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])
    await compute(client, payrun)

    payslip = (
        await client.get(f"/api/v1/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()["items"][0]
    assert payslip["contract"]["id"] == first["id"]
    assert Decimal(payslip["net_amount"]) == Decimal("30000.00")


async def test_a_contract_covering_only_part_of_the_period_warns(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """A mid-period start is a real, computable situation — a joiner — so it
    produces a payslip AND a blocking `contract_gap` warning, rather than
    being silently prorated by a rule nobody wrote."""
    employee = await make_employee(client)
    await make_contract(client, employee["id"], wage="30000.00", start_date="2025-03-15")

    rule = await make_rule(
        client, "FLAT", SalaryRuleComputation.FIXED, SalaryRuleCategory.NET, amount="500.00"
    )
    structure = await make_structure(client, [(rule, 10)])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])
    result = (await compute(client, payrun)).json()

    assert result["computed_count"] == 1
    assert result["blocking_issues"]["contract_gap"] == 1

    payslip = (
        await client.get(f"/api/v1/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()["items"][0]
    gap = [w for w in payslip["warnings"] if w["code"] == "contract_gap"]
    assert len(gap) == 1
    assert gap[0]["severity"] == "blocking"


# ===========================================================================
# Warning generation and the validation firewall
# ===========================================================================


async def test_missing_bank_details_and_missing_checkout_are_blocking_warnings(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """Two of PRD §5.10's three named categories, generated deterministically.

    The open attendance record is on a day in the past, so
    `derive_attendance_status` reads it as `missing_checkout` — and the day is
    NOT counted as worked, because its hours are unknown.
    """
    employee = await make_employee(client, bank_account=None)
    await make_contract(client, employee["id"], wage="30000.00")
    await make_attendance(client, employee["id"], date(2025, 3, 3))
    await make_attendance(client, employee["id"], date(2025, 3, 4), check_out=False)

    rule = await make_rule(
        client,
        "DAYS",
        SalaryRuleComputation.FORMULA,
        SalaryRuleCategory.NET,
        expression="WORKED_DAYS * 100",
    )
    structure = await make_structure(client, [(rule, 10)])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])
    result = (await compute(client, payrun)).json()

    assert result["blocking_issues"]["missing_bank_details"] == 1
    assert result["blocking_issues"]["missing_checkout"] == 1

    payslip = (
        await client.get(f"/api/v1/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()["items"][0]
    codes = {w["code"] for w in payslip["warnings"]}
    assert {"missing_bank_details", "missing_checkout"} <= codes
    # The un-checked-out day is excluded from WORKED_DAYS: one counted day.
    assert Decimal(payslip["worked_days"]) == Decimal("1.00")
    assert Decimal(payslip["net_amount"]) == Decimal("100.00")


async def test_a_second_payrun_over_the_same_period_warns_about_the_duplicate(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """PRD §5.9's "duplicate payslip attempt": paying the same period twice is
    caught and named, not silently allowed."""
    employee = await make_employee(client)
    await make_contract(client, employee["id"], wage="30000.00")
    rule = await make_rule(
        client, "FLAT", SalaryRuleComputation.FIXED, SalaryRuleCategory.NET, amount="500.00"
    )
    structure = await make_structure(client, [(rule, 10)])

    first_run = await make_payrun(client, structure["id"], [employee["id"]])
    await compute(client, first_run)

    second_run = await make_payrun(client, structure["id"], [employee["id"]], name="March again")
    result = (await compute(client, second_run)).json()

    assert result["blocking_issues"]["duplicate_payslip"] == 1
    payslip = (
        await client.get(f"/api/v1/payruns/{second_run['id']}/payslips", headers=PAYROLL_USER)
    ).json()["items"][0]
    duplicate = [w for w in payslip["warnings"] if w["code"] == "duplicate_payslip"][0]
    assert duplicate["severity"] == "blocking"
    assert duplicate["references"], "the warning must name the payslip it duplicates"


async def test_a_contract_naming_another_structure_is_an_advisory_not_a_silent_swap(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """The one genuinely ambiguous semantic in this phase, pinned.

    `Payrun` is "one execution of one SalaryStructure over one period", so the
    PAYRUN's structure runs. A contract pointing at a different structure does
    not silently redirect the computation; it raises an advisory so the
    discrepancy is visible to a human.
    """
    employee = await make_employee(client)
    payrun_rule = await make_rule(
        client, "RUNRULE", SalaryRuleComputation.FIXED, SalaryRuleCategory.NET, amount="777.00"
    )
    payrun_structure = await make_structure(client, [(payrun_rule, 10)])
    other_rule = await make_rule(
        client, "OTHERRULE", SalaryRuleComputation.FIXED, SalaryRuleCategory.NET, amount="111.00"
    )
    other_structure = await make_structure(client, [(other_rule, 10)])

    await make_contract(
        client, employee["id"], wage="30000.00", salary_structure_id=other_structure["id"]
    )

    payrun = await make_payrun(client, payrun_structure["id"], [employee["id"]])
    await compute(client, payrun)

    payslip = (
        await client.get(f"/api/v1/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()["items"][0]
    mismatch = [w for w in payslip["warnings"] if w["code"] == "structure_mismatch"]
    assert len(mismatch) == 1
    assert mismatch[0]["severity"] == "advisory", "an advisory must not block finalization"
    # The PAYRUN's structure ran, and its figure is what reached the payslip.
    assert Decimal(payslip["net_amount"]) == Decimal("777.00")
    assert payslip["lines"][0]["code"] == payrun_rule["code"]


async def test_validate_refuses_while_blocking_issues_stand_and_passes_once_clean(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """PRD §5.10's gate, end to end: refused, fixed, recomputed, accepted.

    The 409 body carries the full report, so the screen that showed the
    failure can render the reason without a second request.
    """
    employee = await make_employee(client, bank_account=None)
    await attach_working_schedule(client, employee["id"])
    await make_contract(client, employee["id"], wage="30000.00")
    rule = await make_rule(
        client, "FLAT", SalaryRuleComputation.FIXED, SalaryRuleCategory.NET, amount="500.00"
    )
    structure = await make_structure(client, [(rule, 10)])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])
    await compute(client, payrun)

    current = (await client.get(f"/api/v1/payruns/{payrun['id']}", headers=PAYROLL_USER)).json()
    refused = await client.post(
        f"/api/v1/payruns/{payrun['id']}/validate",
        json={"version": current["version"]},
        headers=PAYROLL_USER,
    )
    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert detail["report"]["blocking_by_code"]["missing_bank_details"] == 1
    assert detail["report"]["issues"], "the refusal must say which records to open"

    # Fix the offending record, then RECOMPUTE — pressing Validate again
    # without recomputing must not pass, because the payslip still carries the
    # warning that was computed with it.
    still_stale = await client.post(
        f"/api/v1/payruns/{payrun['id']}/validate",
        json={"version": current["version"]},
        headers=PAYROLL_USER,
    )
    assert still_stale.status_code == 409

    await client.patch(
        f"/api/v1/employees/{employee['id']}",
        json={"bank_account": "GB00FIXED0001"},
        headers=HR_MANAGER,
    )
    current = (await client.get(f"/api/v1/payruns/{payrun['id']}", headers=PAYROLL_USER)).json()
    await compute(client, current)

    current = (await client.get(f"/api/v1/payruns/{payrun['id']}", headers=PAYROLL_USER)).json()
    accepted = await client.post(
        f"/api/v1/payruns/{payrun['id']}/validate",
        json={"version": current["version"]},
        headers=PAYROLL_USER,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["blocking_count"] == 0

    after = (await client.get(f"/api/v1/payruns/{payrun['id']}", headers=PAYROLL_USER)).json()
    assert after["status"] == PayrunStatus.VALIDATED.value


async def test_advisory_warnings_do_not_block_validation(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """An employee with no attendance gets a `no_attendance` advisory and the
    run still validates — an absent month is a real payroll outcome, not an
    error."""
    employee = await make_employee(client)
    await attach_working_schedule(client, employee["id"])
    await make_contract(client, employee["id"], wage="30000.00")
    rule = await make_rule(
        client, "FLAT", SalaryRuleComputation.FIXED, SalaryRuleCategory.NET, amount="500.00"
    )
    structure = await make_structure(client, [(rule, 10)])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])
    result = (await compute(client, payrun)).json()

    assert result["blocking_issues"] == {}
    payslip = (
        await client.get(f"/api/v1/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()["items"][0]
    assert {w["code"] for w in payslip["warnings"]} == {"no_attendance"}

    current = (await client.get(f"/api/v1/payruns/{payrun['id']}", headers=PAYROLL_USER)).json()
    validated = await client.post(
        f"/api/v1/payruns/{payrun['id']}/validate",
        json={"version": current["version"]},
        headers=PAYROLL_USER,
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["advisory_count"] == 1


# ===========================================================================
# Finality — Mark Paid and payslip immutability
# ===========================================================================


async def _clean_run(client) -> tuple[dict, dict]:
    """A payrun with one clean, warning-free payslip, computed and validated."""
    employee = await make_employee(client)
    await attach_working_schedule(client, employee["id"])
    await make_contract(client, employee["id"], wage="30000.00")
    await make_attendance(client, employee["id"], date(2025, 3, 3))
    rule = await make_rule(
        client, "FLAT", SalaryRuleComputation.FIXED, SalaryRuleCategory.NET, amount="500.00"
    )
    structure = await make_structure(client, [(rule, 10)])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])
    await compute(client, payrun)

    current = (await client.get(f"/api/v1/payruns/{payrun['id']}", headers=PAYROLL_USER)).json()
    validated = await client.post(
        f"/api/v1/payruns/{payrun['id']}/validate",
        json={"version": current["version"]},
        headers=PAYROLL_USER,
    )
    assert validated.status_code == 200, validated.text
    current = (await client.get(f"/api/v1/payruns/{payrun['id']}", headers=PAYROLL_USER)).json()
    return current, employee


async def test_mark_paid_requires_validation_first(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """Validate is the step that runs the blocking checks, so Mark Paid cannot
    be reached without it — otherwise the firewall is optional."""
    employee = await make_employee(client)
    await make_contract(client, employee["id"], wage="30000.00")
    rule = await make_rule(
        client, "FLAT", SalaryRuleComputation.FIXED, SalaryRuleCategory.NET, amount="500.00"
    )
    structure = await make_structure(client, [(rule, 10)])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])
    await compute(client, payrun)

    current = (await client.get(f"/api/v1/payruns/{payrun['id']}", headers=PAYROLL_USER)).json()
    premature = await client.post(
        f"/api/v1/payruns/{payrun['id']}/mark-paid",
        json={"version": current["version"]},
        headers=PAYROLL_USER,
    )
    assert premature.status_code == 409
    assert "validated" in premature.text.lower()


async def test_a_paid_payrun_and_its_payslips_are_immutable(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """The finality wall (PS B6: "finalized runs preserved as history").

    Every mutation path is checked, because remembering the guard on one and
    forgetting it on another is exactly how a paid payslip changes: recompute,
    edit, delete the run, and delete a payslip.
    """
    payrun, _ = await _clean_run(client)
    paid = await client.post(
        f"/api/v1/payruns/{payrun['id']}/mark-paid",
        json={"version": payrun["version"]},
        headers=PAYROLL_USER,
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["status"] == PayrunStatus.PAID.value

    current = (await client.get(f"/api/v1/payruns/{payrun['id']}", headers=PAYROLL_USER)).json()
    payslip = (
        await client.get(f"/api/v1/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()["items"][0]
    assert payslip["status"] == PayslipStatus.PAID.value

    recompute = await client.post(
        f"/api/v1/payruns/{payrun['id']}/compute",
        json={"version": current["version"]},
        headers={**PAYROLL_USER, **idem()},
    )
    assert recompute.status_code == 409
    assert "never recomputed in place" in recompute.text

    edit = await client.patch(
        f"/api/v1/payruns/{payrun['id']}",
        json={"name": "Renamed after payment", "version": current["version"]},
        headers=PAYROLL_USER,
    )
    assert edit.status_code == 409

    delete_run = await client.delete(
        f"/api/v1/payruns/{payrun['id']}?version={current['version']}", headers=PAYROLL_MANAGER
    )
    assert delete_run.status_code == 409

    delete_payslip = await client.delete(
        f"/api/v1/payslips/{payslip['id']}?version={payslip['version']}", headers=PAYROLL_MANAGER
    )
    assert delete_payslip.status_code == 409
    assert "immutable" in delete_payslip.text

    # And the payslip is still there, unchanged.
    after = (await client.get(f"/api/v1/payslips/{payslip['id']}", headers=PAYROLL_USER)).json()
    assert after["status"] == PayslipStatus.PAID.value
    assert Decimal(after["net_amount"]) == Decimal("500.00")


async def test_send_payslips_requires_a_paid_run_and_then_enqueues(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """The enqueue boundary, and nothing past it.

    This phase's responsibility ends when the run is handed to the queue;
    rendering the PDF and sending the mail is PS B8 / Phase 5, implemented
    separately. The assertions are therefore about the HANDOFF — refused
    before payment, accepted after, with a task id — and deliberately not
    about any document or email, because this phase produces neither.
    """
    payrun, _ = await _clean_run(client)

    too_early = await client.post(
        f"/api/v1/payruns/{payrun['id']}/send-payslips", headers=PAYROLL_USER
    )
    assert too_early.status_code == 409
    assert "paid" in too_early.text.lower()

    await client.post(
        f"/api/v1/payruns/{payrun['id']}/mark-paid",
        json={"version": payrun["version"]},
        headers=PAYROLL_USER,
    )

    queued = await client.post(f"/api/v1/payruns/{payrun['id']}/send-payslips", headers=PAYROLL_USER)
    assert queued.status_code == 202, queued.text
    body = queued.json()
    assert body["payslip_count"] == 1
    assert body["task_id"]


# ===========================================================================
# The wizard, eligibility, and RBAC
# ===========================================================================


async def test_eligible_employees_lists_only_those_the_engine_could_pay(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """PS B5 step 2's candidate list uses the same predicate Compute uses, so
    the wizard cannot offer somebody it will then skip."""
    payable = await make_employee(client)
    await make_contract(client, payable["id"], wage="30000.00")
    joiner = await make_employee(client)
    await make_contract(client, joiner["id"], wage="30000.00", start_date="2025-03-15")
    contractless = await make_employee(client)

    response = await client.get(
        f"/api/v1/payruns/eligible-employees?period_start={PERIOD_START}&period_end={PERIOD_END}",
        headers=PAYROLL_USER,
    )
    assert response.status_code == 200, response.text
    by_id = {row["employee"]["id"]: row for row in response.json()["items"]}

    assert payable["id"] in by_id
    assert by_id[payable["id"]]["partial_period"] is False
    assert joiner["id"] in by_id
    assert by_id[joiner["id"]]["partial_period"] is True, "a mid-period start must be flagged"
    assert contractless["id"] not in by_id


async def test_a_payrun_requires_an_explicit_non_empty_selection(client, cleanup_salary_config):
    """PS B5 calls the selection explicit. An empty list is refused rather
    than being read as "everybody"."""
    rule = await make_rule(
        client, "FLAT", SalaryRuleComputation.FIXED, SalaryRuleCategory.NET, amount="500.00"
    )
    structure = await make_structure(client, [(rule, 10)])

    response = await client.post(
        "/api/v1/payruns/",
        json={
            "name": "Everybody, surely",
            "salary_structure_id": structure["id"],
            "period_start": str(PERIOD_START),
            "period_end": str(PERIOD_END),
            "employee_ids": [],
        },
        headers=headers(PAYROLL_USER, with_key=True),
    )
    assert response.status_code == 422


async def test_a_structure_with_no_active_rules_is_refused_at_compute(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """A run that would produce an empty payslip for everyone is a
    configuration mistake, and it is a fact about the RUN, not about any one
    employee — so it is refused once, up front, rather than surfacing as a
    silent zero on every payslip."""
    employee = await make_employee(client)
    await make_contract(client, employee["id"], wage="30000.00")
    structure = await make_structure(client, [])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])

    refused = await compute(client, payrun, expect=409)
    assert "no active salary rules" in refused.text


async def test_payroll_is_closed_to_hr_manager_and_employee(client, cleanup_salary_config):
    """The sharpest line in Architecture §5's matrix: HR Manager has NO
    payroll access — not even read. Checked on a read, because a matrix
    mistake usually shows up as "well, surely they can look"."""
    for role_headers in (HR_MANAGER, EMPLOYEE):
        listing = await client.get("/api/v1/payruns/", headers=role_headers)
        assert listing.status_code == 403, listing.text
        payslips = await client.get("/api/v1/payslips/", headers=role_headers)
        assert payslips.status_code == 403


async def test_payroll_user_may_run_payroll_but_not_delete_it(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """PRD §3: HR Payroll User holds Create/Read/Update; Delete belongs to HR
    Payroll Manager."""
    employee = await make_employee(client)
    await make_contract(client, employee["id"], wage="30000.00")
    rule = await make_rule(
        client, "FLAT", SalaryRuleComputation.FIXED, SalaryRuleCategory.NET, amount="500.00"
    )
    structure = await make_structure(client, [(rule, 10)])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])
    await compute(client, payrun)

    current = (await client.get(f"/api/v1/payruns/{payrun['id']}", headers=PAYROLL_USER)).json()
    denied = await client.delete(
        f"/api/v1/payruns/{payrun['id']}?version={current['version']}", headers=PAYROLL_USER
    )
    assert denied.status_code == 403

    allowed = await client.delete(
        f"/api/v1/payruns/{payrun['id']}?version={current['version']}", headers=PAYROLL_MANAGER
    )
    assert allowed.status_code == 200, allowed.text


# ===========================================================================
# The context builder, as a unit
# ===========================================================================


def test_period_days_is_inclusive_at_both_ends():
    """The same inclusive convention as the contract EXCLUDE constraint's
    `'[]'` bounds and Phase 2's leave durations — one convention everywhere,
    so a boundary day is never counted by one rule and skipped by another."""
    days = period_days(date(2025, 3, 1), date(2025, 3, 3))
    assert days == [date(2025, 3, 1), date(2025, 3, 2), date(2025, 3, 3)]
    assert period_days(date(2025, 3, 3), date(2025, 3, 3)) == [date(2025, 3, 3)]
    assert period_days(date(2025, 3, 3), date(2025, 3, 1)) == []


async def test_overlapping_approved_leave_days_are_counted_once(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """Two approved requests covering an overlapping stretch contribute the
    UNION of their days, not the sum of their durations — a day off is one day
    off however many requests mention it."""
    employee = await make_employee(client)
    await make_contract(client, employee["id"], wage="30000.00")
    await make_payroll_leave(client, employee["id"], date(2025, 3, 10), date(2025, 3, 12))
    await make_payroll_leave(client, employee["id"], date(2025, 3, 11), date(2025, 3, 13))

    rule = await make_rule(
        client,
        "LEAVE",
        SalaryRuleComputation.FORMULA,
        SalaryRuleCategory.NET,
        expression="UNPAID_LEAVE_DAYS",
    )
    structure = await make_structure(client, [(rule, 10)])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])
    await compute(client, payrun)

    payslip = (
        await client.get(f"/api/v1/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()["items"][0]
    # 10, 11, 12, 13 — four distinct days, not the six the two durations sum to.
    assert Decimal(payslip["net_amount"]) == Decimal("4.00")


async def test_leave_outside_the_period_is_clipped_to_it(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """A request that straddles the period boundary contributes only the days
    inside it — which is why `TimeOffRequest.duration` (the whole request) is
    not what payroll reads."""
    employee = await make_employee(client)
    await make_contract(client, employee["id"], wage="30000.00")
    await make_payroll_leave(client, employee["id"], date(2025, 2, 25), date(2025, 3, 2))

    rule = await make_rule(
        client,
        "LEAVE",
        SalaryRuleComputation.FORMULA,
        SalaryRuleCategory.NET,
        expression="UNPAID_LEAVE_DAYS",
    )
    structure = await make_structure(client, [(rule, 10)])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])
    await compute(client, payrun)

    payslip = (
        await client.get(f"/api/v1/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()["items"][0]
    # 1 and 2 March only; 25-28 February belong to another period's payrun.
    assert Decimal(payslip["net_amount"]) == Decimal("2.00")


async def test_leave_of_a_non_payroll_type_is_invisible_to_payroll(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """`payroll_integration=False` means exactly that: the absence exists, is
    approved, and does not reach the computation context."""
    employee = await make_employee(client)
    await make_contract(client, employee["id"], wage="30000.00")

    type_response = await client.post(
        "/api/v1/time-off-types/",
        json={
            "name": "Paid Annual",
            "code": f"ANNUAL_{uuid.uuid4().hex[:6].upper()}",
            "unit": "days",
            "requires_allocation": False,
            "requires_approval": True,
            "payroll_integration": False,
        },
        headers=HR_MANAGER,
    )
    request = (
        await client.post(
            "/api/v1/time-off-requests/",
            json={
                "employee_id": employee["id"],
                "time_off_type_id": type_response.json()["id"],
                "date_from": "2025-03-10",
                "date_to": "2025-03-14",
            },
            headers=HR_MANAGER,
        )
    ).json()
    await client.post(
        f"/api/v1/time-off-requests/{request['id']}/approve",
        json={"version": request["version"], "decision_note": "ok"},
        headers=HR_MANAGER,
    )

    rule = await make_rule(
        client,
        "LEAVE",
        SalaryRuleComputation.FORMULA,
        SalaryRuleCategory.NET,
        expression="UNPAID_LEAVE_DAYS",
    )
    structure = await make_structure(client, [(rule, 10)])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])
    await compute(client, payrun)

    payslip = (
        await client.get(f"/api/v1/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()["items"][0]
    assert Decimal(payslip["net_amount"]) == Decimal("0.00")


async def test_a_pending_leave_request_does_not_reduce_pay(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """Phase 2 is explicit that a pending request reserves nothing. Payroll
    honours the same rule: only an APPROVED absence reaches the context."""
    employee = await make_employee(client)
    await make_contract(client, employee["id"], wage="30000.00")

    type_response = await client.post(
        "/api/v1/time-off-types/",
        json={
            "name": "Unpaid Pending",
            "code": f"PEND_{uuid.uuid4().hex[:6].upper()}",
            "unit": "days",
            "requires_allocation": False,
            "requires_approval": True,
            "payroll_integration": True,
        },
        headers=HR_MANAGER,
    )
    await client.post(
        "/api/v1/time-off-requests/",
        json={
            "employee_id": employee["id"],
            "time_off_type_id": type_response.json()["id"],
            "date_from": "2025-03-10",
            "date_to": "2025-03-14",
        },
        headers=HR_MANAGER,
    )

    rule = await make_rule(
        client,
        "LEAVE",
        SalaryRuleComputation.FORMULA,
        SalaryRuleCategory.NET,
        expression="UNPAID_LEAVE_DAYS",
    )
    structure = await make_structure(client, [(rule, 10)])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])
    await compute(client, payrun)

    payslip = (
        await client.get(f"/api/v1/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()["items"][0]
    assert Decimal(payslip["net_amount"]) == Decimal("0.00")


async def test_no_float_reaches_a_payslip(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """Architecture §10, checked against the database rather than the API.

    A wage of 0.10 * 3 is the canonical binary-float failure (0.30000000000000004);
    reading the persisted column back as `Decimal` and comparing exactly is
    what proves no float existed anywhere along the path.
    """
    employee = await make_employee(client)
    await make_contract(client, employee["id"], wage="0.10")
    rule = await make_rule(
        client,
        "TRIPLE",
        SalaryRuleComputation.FORMULA,
        SalaryRuleCategory.NET,
        expression="CONTRACT_WAGE * 3",
    )
    structure = await make_structure(client, [(rule, 10)])
    payrun = await make_payrun(client, structure["id"], [employee["id"]])
    await compute(client, payrun)

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(Payslip).where(Payslip.payrun_id == _decode(payrun["id"], "prun"))
            )
        ).scalar_one()
        assert isinstance(row.net_amount, Decimal)
        assert row.net_amount == Decimal("0.30")
        assert str(row.net_amount) == "0.30"
