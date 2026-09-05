"""Phase 4 gaps: one new LOP golden, one checkout firewall regression,
one paid-history regression, plus invalid-input/snapshot migration coverage.
Existing golden tests are unchanged.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.payroll import Payslip
from app.models.employee import Employee
from app.models.contract import Contract
from app.models.salary import SalaryRule, SalaryStructure
from app.seed import _seed_salary_structure
from app.services.payroll_context import build_payroll_context
from app.services.payslip_snapshot import payslip_response
from app.services.salary_resolver import SEED_CONTEXT_NAMES
from tests.test_payroll_api import (
    HR_MANAGER,
    PAYROLL_USER,
    PERIOD_START,
    PERIOD_END,
    make_employee,
    make_contract,
    make_attendance,
    make_payroll_leave,
    make_payrun,
    compute,
    standard_structure,
    attach_working_schedule,
)


async def current_run(client, run):
    result = await client.get(f"/api/v1/payruns/{run['id']}", headers=PAYROLL_USER)
    assert result.status_code == 200, result.text
    return result.json()


async def transition(client, run, action, expected=200):
    current = await current_run(client, run)
    result = await client.post(
        f"/api/v1/payruns/{run['id']}/{action}",
        headers=PAYROLL_USER,
        json={"version": current["version"]},
    )
    assert result.status_code == expected, result.text
    return result


async def slips(client, run):
    result = await client.get(
        f"/api/v1/payruns/{run['id']}/payslips", headers=PAYROLL_USER
    )
    assert result.status_code == 200, result.text
    return result.json()["items"]


async def test_golden_three_unpaid_days_lop_is_exact(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """Paper working, independent of implementation:
    March 2025 starts Saturday. Mon-Fri dates are 3-7, 10-14, 17-21,
    24-28, 31: 5+5+5+5+1 = 21 scheduled working days.
    Contract wage = 30000.00; approved unpaid March 10-12 = 3 days.
    LOP = (30000 / 21) * 3 = 4285.714285... -> 4285.71 HALF_UP.
    Quantize only the final LOP result, not the intermediate daily rate.
    Demo Basic=30000.00, HRA=12000.00, Gross=42000.00, PT=200.00,
    Loss of Pay=4285.71, Net=42000-200-4285.71=37514.29.

    Employee default is deliberately absent: the CONTRACT schedule override
    must supply all 21 working days. The context is persisted as Decimal
    strings, and the actual deduction/Net are persisted PayslipLine amounts.
    """
    employee = await make_employee(client)
    schedule = await attach_working_schedule(client, employee["id"])
    await client.patch(
        f"/api/v1/employees/{employee['id']}",
        headers=HR_MANAGER,
        json={"default_schedule_id": None},
    )
    contract = await make_contract(
        client, employee["id"], wage="30000.00", working_schedule_id=schedule["id"]
    )
    await make_attendance(client, employee["id"], date(2025, 3, 3))
    await make_payroll_leave(
        client, employee["id"], date(2025, 3, 10), date(2025, 3, 12)
    )
    async with AsyncSessionLocal() as session:
        emp = (
            await session.execute(
                select(Employee).where(Employee.public_id == employee["id"])
            )
        ).scalar_one()
        ctr = (
            await session.execute(
                select(Contract).where(Contract.public_id == contract["id"])
            )
        ).scalar_one()
        context = await build_payroll_context(
            session, emp, ctr, PERIOD_START, PERIOD_END
        )
        assert set(context.seed) == SEED_CONTEXT_NAMES
        assert all(isinstance(value, Decimal) for value in context.seed.values())
        assert context.seed["UNPAID_LEAVE_DAYS"] == Decimal("3.00")
        assert context.seed["LOP_AMOUNT"] == Decimal("4285.71")
        structure = await _seed_salary_structure(session)
        structure_id = structure.public_id
        await session.commit()
    run = await make_payrun(client, structure_id, [employee["id"]])
    result = (await compute(client, run)).json()
    assert result["blocking_issues"] == {}
    slip = (await slips(client, run))[0]
    assert [(line["code"], Decimal(line["amount"])) for line in slip["lines"]] == [
        ("PP360_BASIC", Decimal("30000.00")),
        ("PP360_HRA", Decimal("12000.00")),
        ("PP360_GROSS", Decimal("42000.00")),
        ("PP360_PT", Decimal("200.00")),
        ("PP360_LOP", Decimal("4285.71")),
        ("PP360_NET", Decimal("37514.29")),
    ]
    assert (
        next(line for line in slip["lines"] if line["code"] == "PP360_LOP")["category"]
        == "deduction"
    )
    assert Decimal(slip["net_amount"]) == Decimal("37514.29")
    async with AsyncSessionLocal() as session:
        saved = (
            await session.execute(
                select(Payslip).where(Payslip.public_id == slip["id"])
            )
        ).scalar_one()
        assert saved.context_snapshot["LOP_AMOUNT"] == "4285.71"


async def test_missing_checkout_blocks_validate_until_corrected_and_recomputed(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """Isolate missing_checkout: valid bank, contract, and schedule, no other blockers.
    Merely correcting attendance cannot clear the stored firewall finding;
    recompute must replace it before Validate may succeed.
    """
    employee = await make_employee(client)
    await attach_working_schedule(client, employee["id"])
    await make_contract(client, employee["id"], wage="30000.00")
    attendance = await make_attendance(
        client, employee["id"], date(2025, 3, 3), check_out=False
    )
    structure = await standard_structure(client)
    run = await make_payrun(client, structure["id"], [employee["id"]])
    result = (await compute(client, run)).json()
    assert result["blocking_issues"] == {"missing_checkout": 1}
    rejected = await transition(client, run, "validate", 409)
    assert rejected.json()["detail"]["report"]["blocking_by_code"] == {
        "missing_checkout": 1
    }
    assert (await current_run(client, run))["status"] == "computed"
    correction = await client.patch(
        f"/api/v1/attendance/{attendance['id']}",
        headers=HR_MANAGER,
        json={
            "version": attendance["version"],
            "check_in": "2025-03-03T09:00:00Z",
            "check_out": "2025-03-03T17:00:00Z",
            "correction_reason": "Confirmed by HR",
        },
    )
    assert correction.status_code == 200, correction.text
    await transition(client, run, "validate", 409)
    await compute(client, await current_run(client, run))
    accepted = await transition(client, run, "validate")
    assert accepted.json()["blocking_count"] == 0


async def test_paid_payslip_read_and_print_snapshot_are_byte_identical_after_contract_changes(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    """Pay March, close its contract March 31 and start a higher wage April 1.
    Re-fetch every API value, re-render the shared read/print payload and compare
    bytes. Also edit the old contract wage and employee metadata to catch the
    original live-reference leak, and forbid any resolver call during reads.
    PDF rendering itself is Phase 5; its required shared serializer is tested.
    """
    employee = await make_employee(client)
    await attach_working_schedule(client, employee["id"])
    contract = await make_contract(client, employee["id"], wage="30000.00")
    await make_attendance(client, employee["id"], date(2025, 3, 3))
    structure = await standard_structure(client)
    run = await make_payrun(client, structure["id"], [employee["id"]])
    await compute(client, run)
    await transition(client, run, "validate")
    await transition(client, run, "mark-paid")
    slip = (await slips(client, run))[0]
    url = f"/api/v1/payslips/{slip['id']}"
    before = await client.get(url, headers=PAYROLL_USER)
    assert before.status_code == 200, before.text

    async def printed():
        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(Payslip).where(Payslip.public_id == slip["id"])
                )
            ).scalar_one()
            return payslip_response(row).model_dump_json().encode()

    printed_before = await printed()

    async def delivery_payload():
        with patch(
            "app.services.payroll.PayrunService._kick_delivery",
            new_callable=AsyncMock,
            return_value="snapshot-test",
        ) as enqueue:
            result = await client.post(
                f"/api/v1/payruns/{run['id']}/send-payslips", headers=PAYROLL_USER
            )
            assert result.status_code == 202, result.text
            return enqueue.call_args.args[0]

    delivery_before = await delivery_payload()
    closed = await client.patch(
        f"/api/v1/contracts/{contract['id']}",
        headers=HR_MANAGER,
        json={"end_date": "2025-03-31"},
    )
    assert closed.status_code == 200, closed.text
    await make_contract(
        client, employee["id"], wage="36000.00", start_date="2025-04-01"
    )
    amended = await client.patch(
        f"/api/v1/contracts/{contract['id']}",
        headers=HR_MANAGER,
        json={"wage": "31000.00", "job_position": "Edited later"},
    )
    assert amended.status_code == 200, amended.text
    changed_employee = await client.patch(
        f"/api/v1/employees/{employee['id']}",
        headers=HR_MANAGER,
        json={"first_name": "Changed", "work_email": "changed.snapshot@example.com"},
    )
    assert changed_employee.status_code == 200, changed_employee.text
    with patch(
        "app.services.payroll.resolve_salary_structure",
        side_effect=AssertionError("Read recomputed payroll"),
    ):
        after = await client.get(url, headers=PAYROLL_USER)
        assert after.status_code == 200, after.text
        assert after.content == before.content
        assert await printed() == printed_before
        assert (await slips(client, run))[0] == before.json()
        assert await delivery_payload() == delivery_before


@pytest.mark.parametrize("schedule_case", ["missing", "deleted", "zero"])
async def test_unavailable_lop_never_fabricates_amount_and_persists_blocking_warning(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll, schedule_case
):
    employee = await make_employee(client)
    schedule = None
    if schedule_case != "missing":
        schedule = await attach_working_schedule(client, employee["id"])
        if schedule_case == "deleted":
            deleted = await client.delete(
                f"/api/v1/working-schedules/{schedule['id']}", headers=HR_MANAGER
            )
            assert deleted.status_code == 200, deleted.text
    # March 1-2 is a weekend: zero positive hours on a real Mon-Fri schedule.
    start, end = (
        (date(2025, 3, 1), date(2025, 3, 2))
        if schedule_case == "zero"
        else (PERIOD_START, PERIOD_END)
    )
    contract = await make_contract(client, employee["id"], wage="30000.00")
    async with AsyncSessionLocal() as session:
        emp = (
            await session.execute(
                select(Employee).where(Employee.public_id == employee["id"])
            )
        ).scalar_one()
        ctr = (
            await session.execute(
                select(Contract).where(Contract.public_id == contract["id"])
            )
        ).scalar_one()
        context = await build_payroll_context(session, emp, ctr, start, end)
        assert "LOP_AMOUNT" not in context.seed
        assert context.lop_warning.is_blocking
        structure = await _seed_salary_structure(session)
        structure_id = structure.public_id
        await session.commit()
    run = await make_payrun(
        client,
        structure_id,
        [employee["id"]],
        period_start=str(start),
        period_end=str(end),
    )
    result = (await compute(client, run)).json()
    assert result["computed_count"] == 0
    assert result["blocking_issues"] == {"lop_schedule_unavailable": 1}
    assert len(result["skipped"]) == 1
    assert await slips(client, run) == []
    rejected = await transition(client, run, "validate", 409)
    assert (
        rejected.json()["detail"]["report"]["blocking_by_code"][
            "lop_schedule_unavailable"
        ]
        == 1
    )
    # A valid default schedule fixes missing/deleted cases; extending the
    # weekend run isn't allowed, so create a new period for that case.
    if schedule_case != "zero":
        await attach_working_schedule(client, employee["id"])
        result = (await compute(client, await current_run(client, run))).json()
        assert result["blocking_issues"] == {}
        assert result["computed_count"] == 1
        await transition(client, run, "validate")


async def test_legacy_payslip_is_not_backfilled_from_live_contract(
    client, cleanup_employees, cleanup_salary_config, cleanup_payroll
):
    employee = await make_employee(client)
    await attach_working_schedule(client, employee["id"])
    await make_contract(client, employee["id"], wage="30000.00")
    structure = await standard_structure(client)
    run = await make_payrun(client, structure["id"], [employee["id"]])
    await compute(client, run)
    slip = (await slips(client, run))[0]
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(Payslip).where(Payslip.public_id == slip["id"])
            )
        ).scalar_one()
        row.reference_snapshot = None  # Model a pre-migration row.
        await session.commit()
    result = await client.get(f"/api/v1/payslips/{slip['id']}", headers=PAYROLL_USER)
    assert result.status_code == 409
    assert result.json()["detail"]["code"] == "historical_snapshot_unavailable"
    rejected = await transition(client, run, "validate", 409)
    assert (
        rejected.json()["detail"]["report"]["blocking_by_code"][
            "historical_snapshot_unavailable"
        ]
        == 1
    )
    await compute(client, await current_run(client, run))
    assert (await slips(client, run))[0]["contract"]["wage"] == "30000.00"


async def test_demo_salary_seed_is_idempotent(client, cleanup_salary_config):
    async with AsyncSessionLocal() as session:
        first = await _seed_salary_structure(session)
        await session.commit()
        second = await _seed_salary_structure(session)
        await session.commit()
        assert second.id == first.id
        structures = (
            (
                await session.execute(
                    select(SalaryStructure).where(SalaryStructure.code == "PP360_DEMO")
                )
            )
            .scalars()
            .all()
        )
        rules = (
            (
                await session.execute(
                    select(SalaryRule).where(SalaryRule.code.like("PP360_%"))
                )
            )
            .scalars()
            .all()
        )
        assert len(structures) == 1 and len(rules) == 6
