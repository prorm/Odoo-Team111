"""Integration checks for migration 018's deliberate legacy-read 409.

The existing dashboard dataset has pre-snapshot payslips. Use real HTTP and
PostgreSQL to prove the seed preserves them and dashboard aggregates remain
available when the corresponding detail endpoint refuses historical reads.
"""

from decimal import Decimal

from app.core.database import AsyncSessionLocal
from app.models.payroll import Payslip
from app.seed import seed
from sqlalchemy import select

from tests.test_dashboard import PAYROLL_MANAGER, PERIOD_END, PERIOD_START
from tests.test_dashboard import (
    dashboard_dataset as dashboard_dataset,  # noqa: PLC0414 - pytest fixture re-export
)


async def test_seed_does_not_create_or_rewrite_legacy_payslips(
    client, dashboard_dataset
):
    """Every payslip that existed before the seed ran survives it byte for byte.

    Phase 7 gave the seed its own paid demo payrun (`DEMO_PAID_PAYRUN_NAME`),
    so "the seed writes no payslips at all" is no longer the claim — and
    asserting the whole table is unchanged would now be asserting that the
    demo dataset does not exist. The two claims that actually matter are
    checked separately and are both stronger than the old equality:

    1. PRESERVATION — every pre-existing row is identical afterwards, compared
       column by column. This is the real content of migration 018's promise:
       finalized history is never rewritten, repriced or backfilled by a seed.
    2. IDEMPOTENCE — the second `seed()` adds nothing the first did not. A
       seed that computed a fresh July payrun on every run would duplicate
       payslips for the same period, which is precisely the thing
       `duplicate_payslip` exists to catch.
    """

    async def persisted_rows():
        async with AsyncSessionLocal() as session:
            return {
                row["public_id"]: row
                for row in (
                    await session.execute(
                        select(Payslip.__table__).order_by(Payslip.id)
                    )
                )
                .mappings()
                .all()
            }

    before = await persisted_rows()
    assert before and any(
        row["reference_snapshot"] is None for row in before.values()
    )

    await seed()
    after_first = await persisted_rows()
    await seed()
    after_second = await persisted_rows()

    # 1. Preservation: nothing that existed before was touched or removed.
    assert {key: after_second[key] for key in before} == dict(before)
    # 2. Idempotence: the second run created nothing new.
    assert set(after_second) == set(after_first)
    assert after_second == after_first

    legacy_id = next(
        public_id
        for public_id, row in before.items()
        if row["reference_snapshot"] is None
    )
    response = await client.get(
        f"/api/v1/payslips/{legacy_id}", headers=PAYROLL_MANAGER
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "historical_snapshot_unavailable"


async def test_dashboard_totals_and_warnings_survive_legacy_payslip_409(
    client, dashboard_dataset
):
    """Engineering: paid Alice=3000 and Bob=4000, average=3500.

    Alice has a legacy snapshot-less payslip with three stored warnings.
    Her detail returns 409; dashboard still returns exact stored money and
    all warning fields. No dashboard code or fallback behavior is changed.
    """
    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    select(Payslip).where(
                        Payslip.payrun_id.in_(dashboard_dataset["payrun_ids"])
                    )
                )
            )
            .scalars()
            .all()
        )
        alice_slip = next(
            row
            for row in rows
            if row.employee.public_id == dashboard_dataset["alice"]["id"]
        )
        assert alice_slip.reference_snapshot is None
        public_id, stored_warnings = alice_slip.public_id, alice_slip.warnings

    detail_url = f"/api/v1/payslips/{public_id}"
    unavailable = await client.get(detail_url, headers=PAYROLL_MANAGER)
    assert unavailable.status_code == 409
    assert unavailable.json()["detail"]["code"] == "historical_snapshot_unavailable"

    response = await client.get(
        "/api/v1/dashboard/summary",
        headers=PAYROLL_MANAGER,
        params={
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "department_id": dashboard_dataset["engineering"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert Decimal(body["kpis"]["total_net_salary_paid"]) == Decimal("7000.00")
    assert Decimal(body["kpis"]["average_salary"]) == Decimal("3500.00")
    assert body["kpis"]["payslips_generated"] == 3
    observed = [row for row in body["warnings"] if row["payslip_id"] == public_id]
    assert len(observed) == len(stored_warnings) == 3
    assert [
        {key: row[key] for key in ("code", "severity", "message", "references")}
        for row in observed
    ] == stored_warnings
    assert (
        await client.get(detail_url, headers=PAYROLL_MANAGER)
    ).content == unavailable.content
