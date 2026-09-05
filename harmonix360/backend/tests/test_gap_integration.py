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
    async def persisted_rows():
        async with AsyncSessionLocal() as session:
            return (
                (await session.execute(select(Payslip.__table__).order_by(Payslip.id)))
                .mappings()
                .all()
            )

    before = await persisted_rows()
    assert before and any(row["reference_snapshot"] is None for row in before)
    await seed()
    await seed()
    assert await persisted_rows() == before

    legacy_id = next(
        row["public_id"] for row in before if row["reference_snapshot"] is None
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
