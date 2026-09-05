"""Phase 10 read-side features (PRD §5.6-§5.10, Architecture §8.6).

    GET /payslips/{id}/calculation          View Calculation      (§5.6)
    GET /payslips/{id}/comparison           Why Did Pay Change?   (§5.9)
    GET /employees/{id}/contract-timeline   Contract Time Machine (§5.8)
    GET /payruns/{id}/firewall              Validation Firewall   (§5.10)
    GET /anomalies                          Anomaly detection     (§5.7)

EVERY ROUTE HERE IS A GET, AND THAT IS THE DESIGN
--------------------------------------------------
Architecture §8.6 classifies all of these as read-side. None of them writes,
recomputes, or derives an authoritative figure of its own — they render what
the deterministic engine already persisted. The one action any of these screens
offers is Revalidate, which is the EXISTING `POST /payruns/{id}/validate`; this
router does not wrap it, because wrapping it would create a second path to a
transition that already has one (Architecture §9).

If a number on one of these screens ever disagreed with a payslip, the bug
would be in the rendering, not in the payroll — which is why the calculation
tree shows its category subtotals ALONGSIDE the persisted gross and net rather
than in place of them.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import CurrentUser, require_role
from app.core.database import get_db
from app.core.rate_limit import rate_limiter
from app.models.enums import HR_ROLES, PAYROLL_ROLES, EmployeeType
from app.models.payroll import Payslip
from app.repositories.hr import EmployeeRepository
from app.services import anomalies as anomaly_service
from app.services import contract_history, firewall, pay_comparison, payslip_explain
from app.services.dashboard import resolve_filters
from app.services.employee import EmployeeService
from app.services.payroll import PayslipService

router = APIRouter(tags=["Insights"], dependencies=[Depends(rate_limiter)])


async def _payslip_or_404(db: AsyncSession, public_id: str) -> Payslip:
    return await PayslipService(db).read_payslip(public_id)


@router.get(
    "/payslips/{public_id}/calculation",
    summary="Payslip calculation tree — read-only (PRD §5.6)",
)
async def payslip_calculation(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    """The frozen inputs, every rule in the sequence it ran, category
    subtotals, and the persisted totals.

    Nothing is re-evaluated. `inputs.available` is false for a legacy payslip
    with no snapshot, and in that case the inputs are reported as unrecoverable
    rather than reconstructed from the employee's current contract — which
    would describe today's wage, not the one this payslip was paid on.
    """
    payslip = await _payslip_or_404(db, public_id)
    return await payslip_explain.calculation_tree(db, payslip)


@router.get(
    "/payslips/{public_id}/comparison",
    summary="Period-over-period pay change — read-only (PRD §5.9)",
)
async def payslip_comparison(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    """This payslip against the employee's previous one.

    Returns `comparable: false` with a reason when there is no earlier payslip
    — a first payslip is a normal state, not an error, and a client needs to
    say "nothing to compare against" rather than render an empty diff.
    """
    payslip = await _payslip_or_404(db, public_id)
    return await pay_comparison.compare_with_previous(db, payslip)


@router.get(
    "/employees/{public_id}/contract-timeline",
    summary="Contract Time Machine — read-only (PRD §5.8)",
)
async def contract_timeline(
    public_id: str,
    period_start: Optional[date] = Query(None, description="Inclusive. With period_end, highlights the contract that period resolves to."),
    period_end: Optional[date] = Query(None, description="Inclusive."),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(HR_ROLES)),
):
    """Every contract this employee has held, plus the wage change between each
    consecutive pair.

    When a period is supplied, `resolution` names the ONE contract that period
    resolves to — using the payroll engine's own resolver, so the timeline can
    never highlight a different contract than the one an employee was paid
    under. An unresolvable period is returned as information, not an error.
    """
    employee = await EmployeeRepository(db).get_by_public_id(public_id or "")
    if employee is None:
        raise HTTPException(404, f"Employee '{public_id}' not found")
    EmployeeService(db).assert_can_read(current_user, employee)

    timeline = await contract_history.contract_timeline(db, employee)
    if period_start and period_end:
        timeline["resolution"] = await contract_history.contract_for_period(
            db, employee, period_start, period_end
        )
    else:
        timeline["resolution"] = None
    return timeline


@router.get(
    "/payruns/{public_id}/firewall",
    summary="Payroll Validation Firewall — grouped, navigable (PRD §5.10)",
)
async def payrun_firewall(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    """The gate, grouped by finding code with a fix and a navigation target.

    A read-side view over the same `validation_report` the Validate button
    runs. The `revalidate` block names the existing endpoint rather than
    offering a new one.
    """
    return await firewall.firewall_report(db, public_id)


@router.get(
    "/payruns/firewall/open",
    summary="Firewall summary for every unfinalized payrun (PRD §5.10)",
)
async def open_gates(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    return {"payruns": await firewall.open_payrun_gates(db, limit=limit)}


@router.get(
    "/anomalies",
    summary="Deterministic anomaly detection — read-only (PRD §5.7)",
)
async def list_anomalies(
    period_start: Optional[date] = Query(None, description="Inclusive. Defaults to the current month."),
    period_end: Optional[date] = Query(None, description="Inclusive."),
    department_id: Optional[str] = Query(None),
    employee_type: Optional[EmployeeType] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    """Seven deterministic checks, most severe first.

    These are application queries with stated thresholds, not a model's
    opinion — PRD §5.7 is explicit that an LLM never decides whether a payroll
    transaction is valid. Each finding carries the comparison that produced it
    (`current_value` against `baseline`) so a reader can check the arithmetic,
    and a `navigate_to` route so they can act on it.

    `thresholds` is returned alongside so nobody downstream mistakes a
    configured convention for a payroll rule.
    """
    filters = await resolve_filters(
        db,
        period_start=period_start,
        period_end=period_end,
        department_id=department_id,
        employee_type=employee_type,
    )
    findings = await anomaly_service.detect_all(db, filters)
    return {
        "period_start": filters.period_start.isoformat(),
        "period_end": filters.period_end.isoformat(),
        "department_id": filters.department_public_id,
        "summary": await anomaly_service.summarize(findings),
        "anomalies": findings,
        "thresholds": {
            "large_salary_jump_pct": str(anomaly_service.SALARY_JUMP_PCT),
            "low_attendance_pct": str(anomaly_service.LOW_ATTENDANCE_PCT),
            "overtime_hours": str(anomaly_service.OVERTIME_HOURS),
            "contract_expiry_days": anomaly_service.CONTRACT_EXPIRY_DAYS,
            "department_spike_pct": str(anomaly_service.DEPARTMENT_SPIKE_PCT),
            "note": (
                "These are configured conventions chosen for this deployment, not payroll "
                "rules. A finding means a value crossed one of these bounds — nothing more."
            ),
        },
    }
