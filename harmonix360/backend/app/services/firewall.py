"""Payroll Validation Firewall — read-side aggregation (PRD §5.10).

This is a VISIBILITY layer over findings that already exist. It calls
`PayrunService.validation_report`, which re-derives every issue from persisted
state (`Payslip.warnings` plus `Payrun.computation_warnings` plus selected
employees with no payslip), and adds three things a screen needs and a raw
report does not:

    grouping      issues collected by code, so "4 missing bank details" reads
                  as one actionable row instead of four scattered ones
    names         each issue's employee resolved to a person, because
                  "emp_wG2A92xE has no bank account" is not a sentence anyone
                  can act on
    navigation    a concrete route per issue — PRD §5.10's "navigation
                  straight to the offending records"

WHAT THIS IS NOT
----------------
It is not a second validation engine. It computes no severity of its own,
invents no finding, and cannot clear one. Every issue it renders was produced
by the Architecture §7 step 6 checks, and the only way to make one go away is
to fix the underlying record and recompute. "Revalidate" in the UI is the
EXISTING `POST /payruns/{id}/validate` — this module has no write path at all.

The distinction matters because a firewall that could suppress a finding would
be a way to finalize a payrun the engine refused.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.models.enums import PayrunStatus
from app.models.payroll import Payrun
from app.services.payroll import PayrunService
from app.services.payroll_context import BLOCKING

#: What a person should do about each code, and where to go to do it. Keyed by
#: the engine's own warning codes — this table never invents a code, and an
#: unrecognised one falls through to a generic entry rather than being hidden.
#: Hiding an unknown finding would be the single most dangerous behaviour
#: available to this module: a blocking issue nobody can see still blocks.
_GUIDANCE: dict[str, dict[str, str]] = {
    "missing_bank_details": {
        "title": "Missing bank details",
        "fix": "Add a bank account to the employee record, then recompute the payrun.",
        "route": "/employees",
    },
    "missing_checkout": {
        "title": "Missing checkout",
        "fix": "Correct the attendance record, then recompute the payrun.",
        "route": "/attendance",
    },
    "duplicate_payslip": {
        "title": "Duplicate payslip",
        "fix": "This period is already covered by another payslip. Remove the overlap or drop the employee from this run.",
        "route": "/payroll",
    },
    "contract_gap": {
        "title": "Contract does not cover the whole period",
        "fix": "Check the contract dates. Pay is prorated by worked days, not by contract coverage.",
        "route": "/contracts",
    },
    "lop_schedule_unavailable": {
        "title": "Loss of Pay cannot be calculated",
        "fix": "The employee has no usable working schedule for this period. Fix the schedule, then recompute.",
        "route": "/employees/schedules",
    },
    "no_payslip": {
        "title": "Selected but not computed",
        "fix": "Payroll could not resolve a contract or a required input for this employee. Fix the cause, then recompute.",
        "route": "/payroll",
    },
    "historical_snapshot_unavailable": {
        "title": "Legacy payslip without a snapshot",
        "fix": "This payslip predates the input snapshot and cannot be read or finalized. Recompute the run.",
        "route": "/payroll",
    },
}

_GENERIC = {
    "title": "Payroll finding",
    "fix": "Review the affected record, then recompute the payrun.",
    "route": "/payroll",
}


async def _employee_names(session: AsyncSession, public_ids: set[str]) -> dict[str, str]:
    if not public_ids:
        return {}
    rows = (
        await session.execute(
            select(Employee.public_id, Employee.first_name, Employee.last_name).where(
                Employee.public_id.in_(public_ids)
            )
        )
    ).all()
    return {public_id: f"{first} {last}" for public_id, first, last in rows}


async def firewall_report(session: AsyncSession, payrun_public_id: str) -> dict:
    """The grouped, navigable view of one payrun's gate."""
    service = PayrunService(session)
    payrun = await service.read_payrun(payrun_public_id)
    report = await service.validation_report(payrun)

    issues = report["issues"]
    names = await _employee_names(
        session, {issue["employee_id"] for issue in issues if issue.get("employee_id")}
    )

    groups: dict[str, dict] = {}
    for issue in issues:
        code = issue.get("code", "unknown")
        guidance = _GUIDANCE.get(code, _GENERIC)
        group = groups.setdefault(
            code,
            {
                "code": code,
                "title": guidance["title"],
                "fix": guidance["fix"],
                "severity": issue.get("severity", BLOCKING),
                "recognized": code in _GUIDANCE,
                "count": 0,
                "issues": [],
            },
        )
        # A code carrying a blocking instance anywhere is presented as blocking:
        # the group's badge must reflect the worst thing inside it, or a
        # blocking issue hides behind an advisory heading.
        if issue.get("severity") == BLOCKING:
            group["severity"] = BLOCKING
        group["count"] += 1
        employee_id = issue.get("employee_id")
        group["issues"].append(
            {
                "message": issue.get("message", ""),
                "severity": issue.get("severity", BLOCKING),
                "employee_id": employee_id,
                "employee_name": names.get(employee_id) if employee_id else None,
                "payslip_id": issue.get("payslip_id"),
                "references": issue.get("references", []),
                "navigate_to": (
                    f"/employees/{employee_id}"
                    if employee_id and guidance["route"] == "/employees"
                    else guidance["route"]
                ),
            }
        )

    ordered = sorted(
        groups.values(), key=lambda group: (group["severity"] != BLOCKING, -group["count"])
    )

    return {
        "payrun_id": report["payrun_id"],
        "payrun_name": payrun.name,
        "period_start": payrun.period_start.isoformat(),
        "period_end": payrun.period_end.isoformat(),
        "status": payrun.status.value
        if isinstance(payrun.status, PayrunStatus)
        else str(payrun.status),
        "version": payrun.version,
        "blocking_count": report["blocking_count"],
        "advisory_count": report["advisory_count"],
        "blocking_by_code": report["blocking_by_code"],
        "can_validate": report["blocking_count"] == 0
        and payrun.status is PayrunStatus.COMPUTED,
        "gate_message": _gate_message(report, payrun),
        "groups": ordered,
        # The Revalidate action is the EXISTING endpoint. Naming it here rather
        # than implementing a second one is the point of this module.
        "revalidate": {
            "method": "POST",
            "path": f"/api/v1/payruns/{payrun.public_id}/validate",
            "requires_version": True,
        },
    }


def _gate_message(report: dict, payrun: Payrun) -> str:
    """One sentence saying exactly why the gate is or is not open."""
    if payrun.status is PayrunStatus.PAID:
        return "This payrun is paid. It is immutable and cannot be recomputed or revalidated."
    if payrun.status is PayrunStatus.VALIDATED:
        return "This payrun is validated and ready to be marked paid."
    if payrun.status is PayrunStatus.DRAFT:
        return "This payrun has not been computed yet. Compute it before validating."
    if report["blocking_count"]:
        return (
            f"{report['blocking_count']} blocking issue(s) must be resolved before this payrun "
            "can be validated. Fixing a record is not enough on its own — recompute the run so "
            "the findings are re-derived."
        )
    if report["advisory_count"]:
        return (
            f"No blocking issues. {report['advisory_count']} advisory finding(s) are shown for "
            "information and do not prevent validation."
        )
    return "No findings. This payrun can be validated."


async def open_payrun_gates(session: AsyncSession, *, limit: int = 10) -> list[dict]:
    """A compact firewall summary for every unfinalized payrun.

    Backs the dashboard's "what needs attention" strip. Only DRAFT and COMPUTED
    runs are included: a validated or paid run has already passed the gate, and
    listing it would make the strip grow forever.
    """
    payruns = (
        (
            await session.execute(
                select(Payrun)
                .where(
                    Payrun.deleted_at.is_(None),
                    Payrun.status.in_([PayrunStatus.DRAFT, PayrunStatus.COMPUTED]),
                )
                .order_by(Payrun.period_start.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )

    service = PayrunService(session)
    summaries = []
    for payrun in payruns:
        report = await service.validation_report(payrun)
        summaries.append(
            {
                "payrun_id": payrun.public_id,
                "name": payrun.name,
                "status": payrun.status.value,
                "period_start": payrun.period_start.isoformat(),
                "period_end": payrun.period_end.isoformat(),
                "blocking_count": report["blocking_count"],
                "advisory_count": report["advisory_count"],
                "blocking_by_code": report["blocking_by_code"],
                "can_validate": report["blocking_count"] == 0
                and payrun.status is PayrunStatus.COMPUTED,
            }
        )
    return summaries


__all__ = ["firewall_report", "open_payrun_gates"]
