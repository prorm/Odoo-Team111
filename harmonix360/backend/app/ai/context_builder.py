"""The AI context layer — authoritative ERP facts, assembled per question.

This module is the reason the AI can answer "why did Rahul's salary drop?"
rather than only "here is Rahul's payslip". It ORCHESTRATES the existing read
services; it does not query payroll tables to derive its own figures and it
contains no business logic. Every number it emits was produced by the
deterministic engine and is copied, not recomputed.

FOUR PROPERTIES EVERY CONTEXT HERE MUST HAVE
--------------------------------------------
1. **Task-specific and scoped.** A payslip question loads that employee's
   contracts, attendance and two payslips — not the department's payroll and
   not every employee. Sending "everything available" is how a model ends up
   confidently relating two facts that have nothing to do with each other, and
   it is also how a prompt stops fitting in a context window.
2. **Traceable.** `sources` names the service each section came from, so any
   sentence in an answer can be walked back to the query that produced it.
3. **Explicit about absence.** `unavailable` carries a plain-English reason for
   anything that could not be established. This is load-bearing: §3 of the phase
   brief requires the model to say the data is insufficient rather than invent a
   cause, and it can only do that if absence reaches it as a fact instead of as
   a silently missing key.
4. **Money as strings.** Architecture §6/§10 — `Decimal` in the application,
   `str()` across every JSON boundary, and an AI prompt is a JSON boundary.
   There is no `float()` in this module.

WHAT THIS MODULE MAY NOT DO
---------------------------
No writes. No rule evaluation. No "expected" or "should be" figure of its own.
If a payslip says NET is 37514.29, that is the number, and the AI's job is to
explain how the ERP got there — never to check it.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.models.enums import PayrunStatus, TimeOffRequestStatus
from app.models.payroll import Payrun, Payslip
from app.models.time_off import TimeOffAllocation, TimeOffRequest
from app.repositories.hr import EmployeeRepository
from app.services import contract_history, pay_comparison, payslip_explain
from app.services.dashboard import DashboardService, ResolvedFilters, resolve_filters
from app.services.payroll_context import (
    ContractResolutionError,
    attendance_facts,
    leave_facts,
    period_days,
)


@dataclass
class AIContext:
    """One assembled, prompt-ready view of authoritative ERP state.

    `facts` is what the model may treat as true. `unavailable` is what it may
    not guess at. `sources` is how a human checks either.
    """

    task_type: str
    question: str
    subject: dict = field(default_factory=dict)
    facts: dict = field(default_factory=dict)
    unavailable: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    def note_unavailable(self, reason: str) -> None:
        if reason and reason not in self.unavailable:
            self.unavailable.append(reason)

    def add(self, name: str, value: Any, *, source: str) -> None:
        self.facts[name] = value
        if source not in self.sources:
            self.sources.append(source)

    def as_payload(self) -> dict:
        """The dict handed to the provider. Deliberately flat and labelled:
        the prompt template renders these three keys under three different
        headings so the model can tell an ERP fact from a gap in the record."""
        return {
            "subject": self.subject,
            "authoritative_facts": self.facts,
            "unavailable_information": self.unavailable,
            "fact_sources": self.sources,
        }


# --------------------------------------------------------------------------
# Small shared helpers
# --------------------------------------------------------------------------


async def _employee_or_none(session: AsyncSession, public_id: str) -> Optional[Employee]:
    return await EmployeeRepository(session).get_by_public_id(public_id or "")


def _employee_identity(employee: Employee) -> dict:
    """Identity and employment facts — no bank account, no phone.

    An AI answer is quoted, pasted and forwarded. `bank_account` is excluded
    for the same reason `EmployeeRef` excludes it: whatever goes in here is
    readable by everyone the answer reaches. Whether bank details are PRESENT
    is a payroll fact and is reported as a boolean by the blocker context; the
    digits themselves are never needed to explain a payslip.
    """
    return {
        "employee_id": employee.public_id,
        "name": employee.full_name,
        "work_email": employee.work_email,
        "job_position": employee.job_position,
        "employment_status": employee.status.value,
        "employment_type": employee.employee_type.value,
        "department": employee.department.name if employee.department else None,
        "department_id": employee.department.public_id if employee.department else None,
        "manager": employee.manager.full_name if employee.manager else None,
        "hire_date": employee.hire_date.isoformat() if employee.hire_date else None,
        "exit_date": employee.exit_date.isoformat() if employee.exit_date else None,
        "default_schedule": employee.default_schedule.name if employee.default_schedule else None,
    }


def _previous_month(period_start: date) -> tuple[date, date]:
    """The calendar month immediately before `period_start`'s month."""
    year = period_start.year - 1 if period_start.month == 1 else period_start.year
    month = 12 if period_start.month == 1 else period_start.month - 1
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


async def _approved_leave_in_period(
    session: AsyncSession, employee: Employee, period_start: date, period_end: date
) -> list[dict]:
    """Approved time off overlapping the period, with its payroll relevance.

    `payroll_integration` and `is_unpaid` come off the leave TYPE, which is what
    decides whether a day of leave reduces pay. Reporting the requests without
    that flag would let a model attribute a pay drop to a paid holiday.
    """
    rows = (
        (
            await session.execute(
                select(TimeOffRequest)
                .where(
                    TimeOffRequest.employee_id == employee.id,
                    TimeOffRequest.deleted_at.is_(None),
                    TimeOffRequest.status == TimeOffRequestStatus.APPROVED,
                    TimeOffRequest.date_from <= period_end,
                    TimeOffRequest.date_to >= period_start,
                )
                .order_by(TimeOffRequest.date_from)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "request_id": row.public_id,
            "type": row.time_off_type.name,
            "type_code": row.time_off_type.code,
            "date_from": row.date_from.isoformat(),
            "date_to": row.date_to.isoformat(),
            "duration": str(row.duration),
            "unit": row.time_off_type.unit.value,
            "affects_payroll": bool(row.time_off_type.payroll_integration),
        }
        for row in rows
    ]


async def _leave_balances(session: AsyncSession, employee: Employee) -> list[dict]:
    rows = (
        (
            await session.execute(
                select(TimeOffAllocation)
                .where(
                    TimeOffAllocation.employee_id == employee.id,
                    TimeOffAllocation.deleted_at.is_(None),
                )
                .order_by(TimeOffAllocation.valid_from)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "allocation_id": row.public_id,
            "type": row.time_off_type.name,
            "type_code": row.time_off_type.code,
            "allocated": str(row.allocated),
            "taken": str(row.taken),
            "remaining": str(row.allocated - row.taken),
            "valid_from": row.valid_from.isoformat(),
            "valid_to": row.valid_to.isoformat() if row.valid_to else None,
            "status": row.status.value,
        }
        for row in rows
    ]


# --------------------------------------------------------------------------
# A. Payslip / payroll explanation
# --------------------------------------------------------------------------


async def build_payslip_explanation_context(
    session: AsyncSession,
    payslip: Payslip,
    *,
    question: str,
    include_department: bool = False,
) -> AIContext:
    """Everything needed to explain ONE payslip, including why it differs from
    the last one.

    The investigation path this assembles is the one PRD §5.1 describes:
    previous payslip -> contract wage change -> attendance -> unpaid leave ->
    salary rules -> current payslip. Each link is a separate section so the
    model can cite the specific one that moved, and so a link that is missing
    is visibly missing rather than quietly absent.
    """
    employee = payslip.employee
    context = AIContext(
        task_type="payslip_explanation",
        question=question,
        subject={
            "payslip_id": payslip.public_id,
            "employee_id": employee.public_id,
            "employee_name": employee.full_name,
        },
    )

    context.add("employee", _employee_identity(employee), source="EmployeeRepository")

    # The payslip itself, through the shared snapshot boundary — the same
    # serializer the REST detail endpoint and the PDF worker use. A legacy row
    # with no snapshot raises there; here that is reported, not propagated.
    tree = await payslip_explain.calculation_tree(session, payslip)
    context.add("current_payslip", tree, source="payslip_explain.calculation_tree")
    if not tree["inputs"]["available"]:
        context.note_unavailable(tree["inputs"]["reason"])

    run = await session.get(Payrun, payslip.payrun_id)
    period_start = run.period_start if run else None
    period_end = run.period_end if run else None

    # Period-over-period diff, entirely from persisted payslips.
    comparison = await pay_comparison.compare_with_previous(session, payslip)
    context.add("comparison_with_previous_period", comparison, source="pay_comparison.compare_with_previous")
    if not comparison["comparable"]:
        context.note_unavailable(comparison["reason"])
    for reason in comparison.get("unavailable", []):
        context.note_unavailable(reason)

    # Contract history — the "wage change" link in the chain.
    timeline = await contract_history.contract_timeline(session, employee)
    context.add(
        "contract_history",
        {
            "contract_count": timeline["contract_count"],
            "contracts": timeline["contracts"],
            "wage_changes": timeline["wage_changes"],
            "has_wage_change": timeline["has_wage_change"],
        },
        source="contract_history.contract_timeline",
    )

    if period_start and period_end:
        resolution = await contract_history.contract_for_period(
            session, employee, period_start, period_end
        )
        context.add("contract_applicable_to_this_period", resolution, source="payroll_context.resolve_period_contract")
        if not resolution["resolved"]:
            context.note_unavailable(resolution["reason"])

        # Attendance and leave, re-derived by the SAME functions the engine
        # uses, so the facts the model sees match the facts that produced the
        # payslip rather than a parallel count.
        try:
            facts = await attendance_facts(session, employee, period_start, period_end)
            context.add(
                "attendance",
                {
                    "worked_days": str(facts.worked_days),
                    "worked_dates": [d.isoformat() for d in facts.worked_dates],
                    "missing_checkout_dates": [d.isoformat() for d in facts.missing_checkout_dates],
                    "calendar_days_in_period": len(period_days(period_start, period_end)),
                    "note": (
                        "worked_days counts distinct dates with recorded attendance; it is not "
                        "the number of days the employee was scheduled to work."
                    ),
                },
                source="payroll_context.attendance_facts",
            )
        except Exception as exc:  # pragma: no cover - defensive, reported not raised
            context.note_unavailable(f"Attendance could not be re-derived for this period: {exc}")

        try:
            leave = await leave_facts(session, employee, period_start, period_end)
            context.add(
                "time_off",
                {
                    "unpaid_leave_days": str(leave.unpaid_leave_days),
                    "payroll_relevant_request_ids": list(leave.request_public_ids),
                    "approved_leave_in_period": await _approved_leave_in_period(
                        session, employee, period_start, period_end
                    ),
                },
                source="payroll_context.leave_facts",
            )
        except Exception as exc:  # pragma: no cover - defensive
            context.note_unavailable(f"Time off could not be re-derived for this period: {exc}")

    # Salary configuration that ran, named from the payslip's own lines.
    if run and run.salary_structure:
        context.add(
            "salary_structure",
            {
                "code": run.salary_structure.code,
                "name": run.salary_structure.name,
                "rules_that_ran": [
                    {"code": line["code"], "name": line["name"], "sequence": line["sequence"]}
                    for line in tree["lines"]
                ],
            },
            source="Payrun.salary_structure + persisted PayslipLine rows",
        )

    if include_department and employee.department and period_start and period_end:
        filters = ResolvedFilters(
            period_start=period_start,
            period_end=period_end,
            department_id=employee.department_id,
            department_public_id=employee.department.public_id,
            employee_type=None,
        )
        service = DashboardService(session)
        totals = await service.salary_cost_by_department(filters)
        context.add(
            "department_payroll_this_period",
            [{**row, "amount": str(row["amount"])} for row in totals],
            source="DashboardService.salary_cost_by_department",
        )

    return context


# --------------------------------------------------------------------------
# B. Department / payroll variance
# --------------------------------------------------------------------------


async def _department_period_facts(
    session: AsyncSession, filters: ResolvedFilters
) -> dict:
    """One period's authoritative department aggregates."""
    service = DashboardService(session)
    costs = await service.salary_cost_by_department(filters)
    breakdown = await service.department_breakdown(filters)
    return {
        "period_start": filters.period_start.isoformat(),
        "period_end": filters.period_end.isoformat(),
        "total_net_salary_paid": str(await service.total_net_salary_paid(filters)),
        "payslips_generated": await service.payslips_generated(filters),
        "average_salary": str(await service.average_salary(filters)),
        "salary_cost_by_department": [
            {**row, "amount": str(row["amount"])} for row in costs
        ],
        "department_breakdown": [
            {
                key: (str(value) if isinstance(value, Decimal) else value)
                for key, value in row.items()
            }
            for row in breakdown
        ],
    }


async def build_department_variance_context(
    session: AsyncSession,
    *,
    question: str,
    department_public_id: Optional[str],
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
) -> AIContext:
    """Current period against the previous calendar month, department-scoped.

    Both periods are fetched through `DashboardService`, which is the same
    query set the Reports screen renders — so an AI answer and the dashboard a
    manager is looking at cannot disagree. The deltas are Decimal subtractions
    of those two authoritative totals.
    """
    filters = await resolve_filters(
        session,
        period_start=period_start,
        period_end=period_end,
        department_id=department_public_id,
        employee_type=None,
    )
    previous_start, previous_end = _previous_month(filters.period_start)
    previous_filters = ResolvedFilters(
        period_start=previous_start,
        period_end=previous_end,
        department_id=filters.department_id,
        department_public_id=filters.department_public_id,
        employee_type=filters.employee_type,
    )

    context = AIContext(
        task_type="payroll_variance",
        question=question,
        subject={
            "department_id": filters.department_public_id,
            "current_period": f"{filters.period_start.isoformat()}..{filters.period_end.isoformat()}",
            "comparison_period": f"{previous_start.isoformat()}..{previous_end.isoformat()}",
        },
    )

    current = await _department_period_facts(session, filters)
    previous = await _department_period_facts(session, previous_filters)
    context.add("current_period", current, source="DashboardService")
    context.add("comparison_period", previous, source="DashboardService")

    current_total = Decimal(current["total_net_salary_paid"])
    previous_total = Decimal(previous["total_net_salary_paid"])
    difference = current_total - previous_total
    movement: dict = {
        "total_net_salary_before": str(previous_total),
        "total_net_salary_after": str(current_total),
        "delta": str(difference),
        "direction": "increase" if difference > 0 else "decrease" if difference < 0 else "unchanged",
        "payslip_count_before": previous["payslips_generated"],
        "payslip_count_after": current["payslips_generated"],
    }
    if previous_total != 0:
        movement["percent_change"] = str(
            (difference / previous_total * Decimal("100")).quantize(Decimal("0.01"))
        )
    else:
        context.note_unavailable(
            "The comparison period has no paid payroll, so a percentage change is undefined. "
            "Report the absolute figures instead of a percentage."
        )
    context.add("movement", movement, source="Decimal difference of two DashboardService totals")

    # The candidate causes, each a fact rather than an attribution. Whether any
    # of them explains the movement is exactly what the model is being asked —
    # and it may only cite what is listed here.
    wage_changes: list[dict] = []
    employees = (
        (
            await session.execute(
                select(Employee).where(
                    Employee.deleted_at.is_(None),
                    *(
                        [Employee.department_id == filters.department_id]
                        if filters.department_id is not None
                        else []
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    for employee in employees:
        timeline = await contract_history.contract_timeline(session, employee)
        for change in timeline["wage_changes"]:
            effective = date.fromisoformat(change["effective_date"])
            if previous_start <= effective <= filters.period_end:
                wage_changes.append(
                    {"employee_id": employee.public_id, "employee_name": employee.full_name, **change}
                )
    context.add(
        "contract_wage_changes_in_window",
        wage_changes,
        source="contract_history.contract_timeline",
    )
    context.add(
        "headcount",
        {
            "employees_in_scope_now": len(employees),
            "note": (
                "Headcount is the current roster in scope. Employees added or removed between "
                "the two periods are visible as a difference in payslip counts above."
            ),
        },
        source="Employee query scoped by the dashboard's department filter",
    )
    return context


# --------------------------------------------------------------------------
# C. What is blocking payroll
# --------------------------------------------------------------------------


async def build_payroll_blockers_context(
    session: AsyncSession,
    *,
    question: str,
    payrun_public_id: Optional[str] = None,
) -> AIContext:
    """The validation firewall's findings, as facts for narration.

    Every issue here was written by the deterministic §7 step 6 checks and is
    read back through `PayrunService.validation_report` — the same call the
    Validate button makes. The AI does not decide what blocks payroll; it
    explains a list the engine produced.
    """
    from app.services.payroll import PayrunService

    context = AIContext(task_type="pending_actions", question=question, subject={})
    service = PayrunService(session)

    if payrun_public_id:
        payruns = [await service.read_payrun(payrun_public_id)]
    else:
        stmt = (
            select(Payrun)
            .where(
                Payrun.deleted_at.is_(None),
                Payrun.status.in_([PayrunStatus.DRAFT, PayrunStatus.COMPUTED]),
            )
            .order_by(Payrun.period_start.desc())
            .limit(5)
        )
        payruns = list((await session.execute(stmt)).scalars().all())

    context.subject = {
        "payrun_ids": [run.public_id for run in payruns],
        "scope": "one payrun" if payrun_public_id else "all unfinalized payruns (most recent 5)",
    }

    if not payruns:
        context.note_unavailable(
            "There is no draft or computed payrun on record, so nothing is currently "
            "waiting to be finalized. Say so rather than describing a hypothetical run."
        )
        return context

    reports = []
    for run in payruns:
        report = await service.validation_report(run)
        reports.append(
            {
                "payrun_id": run.public_id,
                "name": run.name,
                "period_start": run.period_start.isoformat(),
                "period_end": run.period_end.isoformat(),
                "status": run.status.value,
                "report": _stringify(report),
            }
        )
    context.add("payrun_validation_reports", reports, source="PayrunService.validation_report")

    pending = (
        (
            await session.execute(
                select(TimeOffRequest)
                .where(
                    TimeOffRequest.deleted_at.is_(None),
                    TimeOffRequest.status == TimeOffRequestStatus.TO_APPROVE,
                )
                .order_by(TimeOffRequest.date_from)
                .limit(25)
            )
        )
        .scalars()
        .all()
    )
    context.add(
        "time_off_awaiting_approval",
        [
            {
                "request_id": row.public_id,
                "employee_id": row.employee.public_id,
                "employee_name": row.employee.full_name,
                "type": row.time_off_type.name,
                "affects_payroll": bool(row.time_off_type.payroll_integration),
                "date_from": row.date_from.isoformat(),
                "date_to": row.date_to.isoformat(),
                "duration": str(row.duration),
            }
            for row in pending
        ],
        source="TimeOffRequest query (status=to_approve)",
    )
    return context


def _stringify(value: Any) -> Any:
    """Recursively make a service payload JSON-safe with money intact.

    `Decimal` becomes its exact string, never a float — this is the boundary
    Architecture §10 names, and the one place a careless `json.dumps(default=float)`
    would quietly corrupt every amount in an AI prompt.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _stringify(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_stringify(item) for item in value]
    if hasattr(value, "value") and type(value).__mro__[1].__name__ == "str":  # StrEnum
        return value.value
    return value


# --------------------------------------------------------------------------
# D. Anomalies
# --------------------------------------------------------------------------


async def build_anomaly_context(
    session: AsyncSession,
    *,
    question: str,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
    department_public_id: Optional[str] = None,
) -> AIContext:
    """Deterministic anomaly signals for narration.

    The signals come from the application's own queries — never from asking a
    model to look at raw rows and decide what is unusual. PRD §5.7 is explicit:
    "An LLM never decides whether a payroll transaction is valid."

    Phase 10's dedicated anomaly engine registers itself here when present, and
    this function falls back to the dashboard's alert queries when it is not, so
    the AI layer works on either tree.
    """
    filters = await resolve_filters(
        session,
        period_start=period_start,
        period_end=period_end,
        department_id=department_public_id,
        employee_type=None,
    )
    context = AIContext(
        task_type="anomaly_narration",
        question=question,
        subject={
            "period": f"{filters.period_start.isoformat()}..{filters.period_end.isoformat()}",
            "department_id": filters.department_public_id,
        },
    )

    signals = await deterministic_signals(session, filters)

    # Send the findings that matter, not all of them.
    #
    # Every finding is a per-employee row, so the payload grows with headcount:
    # on a five-person roster the whole list fit comfortably, and on a
    # sixty-person one it reached ~13.7k tokens and the provider refused the
    # request outright. The narration then failed for the only reason a reader
    # would never guess — there was too much to say.
    #
    # The COUNTS below are computed over every finding, so the summary the model
    # narrates from stays accurate; only the itemised list is capped. Severity
    # first, so a truncated list drops the least important rows rather than an
    # arbitrary tail, and the cap is declared in the context so the model can
    # say "and N more" instead of implying it saw everything.
    ranked = sorted(
        signals["signals"],
        key=lambda s: (_SEVERITY_ORDER.get(str(s.get("severity", "")).lower(), 99), str(s.get("type", ""))),
    )
    shown = ranked[:_MAX_NARRATED_SIGNALS]
    context.add("deterministic_signals", shown, source=signals["source"])
    context.add(
        "signal_summary",
        {
            "total": len(signals["signals"]),
            "shown_in_detail": len(shown),
            "omitted_from_detail": len(signals["signals"]) - len(shown),
            "by_severity": signals["by_severity"],
            "by_type": signals["by_type"],
        },
        source=signals["source"],
    )
    if len(shown) < len(signals["signals"]):
        context.note_unavailable(
            f"Only the {len(shown)} highest-severity findings are listed individually. "
            f"The totals in signal_summary cover all {len(signals['signals'])}; "
            "do not describe the itemised list as complete."
        )
    if not signals["signals"]:
        context.note_unavailable(
            "No deterministic anomaly check produced a finding for this period and scope. "
            "There is genuinely nothing unusual to report; do not manufacture an observation."
        )
    return context


#: How many individual anomaly findings the narration context itemises.
#: Bounded because the list grows with headcount and the provider has a
#: per-request token ceiling; the SUMMARY still counts every finding.
_MAX_NARRATED_SIGNALS = 20

#: Blocking first. Anomalies use high/medium/low; payslip warnings that reach
#: the same list use blocking/advisory. Anything unrecognised sorts last rather
#: than being dropped or crashing the sort.
_SEVERITY_ORDER = {
    "blocking": 0,
    "high": 1,
    "medium": 2,
    "advisory": 3,
    "low": 4,
}


async def deterministic_signals(session: AsyncSession, filters: ResolvedFilters) -> dict:
    """Structured anomaly signals, from Phase 10's engine when it exists.

    The import is attempted rather than assumed so that this branch works both
    before and after Phase 10 lands — and so the AI layer never silently
    degrades without saying which source it used. `source` is reported into the
    prompt, so an answer built on the fallback is traceable as such.
    """
    try:
        from app.services import anomalies  # type: ignore
    except ImportError:
        anomalies = None  # type: ignore

    if anomalies is not None and hasattr(anomalies, "detect_all"):
        found = await anomalies.detect_all(session, filters)
        rows = [_stringify(item) for item in found]
        source = "anomalies.detect_all (deterministic Phase 10 anomaly engine)"
    else:
        service = DashboardService(session)
        warnings = await service.payroll_warnings(filters)
        attention = await service.contract_attention(filters)
        rows = [
            {
                "type": warning["code"],
                "severity": warning["severity"],
                "message": warning["message"],
                "employee_id": warning.get("employee_id"),
                "employee_name": warning.get("employee_name"),
                "references": warning.get("references", []),
            }
            for warning in warnings
        ] + [_stringify(item) for item in attention]
        source = "DashboardService.payroll_warnings + contract_attention (deterministic queries)"

    by_severity: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for row in rows:
        severity = str(row.get("severity") or "unknown")
        kind = str(row.get("type") or row.get("code") or "unknown")
        by_severity[severity] = by_severity.get(severity, 0) + 1
        by_type[kind] = by_type.get(kind, 0) + 1
    return {"signals": rows, "by_severity": by_severity, "by_type": by_type, "source": source}


# --------------------------------------------------------------------------
# E. General HR context for one employee
# --------------------------------------------------------------------------


async def build_employee_context(
    session: AsyncSession,
    employee: Employee,
    *,
    question: str,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
) -> AIContext:
    """One employee across the HR concepts a question might join: identity,
    contracts, attendance, leave, and their recent payroll.

    This is the context behind "what changed in Rahul's contract?" and "why was
    this employee's pay different from last month?" when the asker has not named
    a payslip.
    """
    context = AIContext(
        task_type="general",
        question=question,
        subject={"employee_id": employee.public_id, "employee_name": employee.full_name},
    )
    context.add("employee", _employee_identity(employee), source="EmployeeRepository")

    timeline = await contract_history.contract_timeline(session, employee)
    context.add("contract_history", timeline, source="contract_history.contract_timeline")

    context.add("leave_balances", await _leave_balances(session, employee), source="TimeOffAllocation query")

    trend = await pay_comparison.payroll_trend(session, employee)
    context.add("recent_payslips", trend, source="pay_comparison.payroll_trend")
    if not trend:
        context.note_unavailable(
            f"{employee.full_name} has no payslips on record, so nothing can be said about "
            "their pay history."
        )

    if period_start and period_end:
        context.add(
            "approved_leave_in_period",
            await _approved_leave_in_period(session, employee, period_start, period_end),
            source="TimeOffRequest query (status=approved)",
        )
        try:
            facts = await attendance_facts(session, employee, period_start, period_end)
            context.add(
                "attendance_in_period",
                {
                    "period_start": period_start.isoformat(),
                    "period_end": period_end.isoformat(),
                    "worked_days": str(facts.worked_days),
                    "missing_checkout_dates": [d.isoformat() for d in facts.missing_checkout_dates],
                },
                source="payroll_context.attendance_facts",
            )
        except ContractResolutionError as exc:  # pragma: no cover - defensive
            context.note_unavailable(exc.reason)

    latest = trend[-1] if trend else None
    if latest:
        payslip = (
            await session.execute(
                select(Payslip).where(Payslip.public_id == latest["payslip_id"])
            )
        ).scalars().first()
        if payslip is not None:
            comparison = await pay_comparison.compare_with_previous(session, payslip)
            context.add(
                "latest_payslip_vs_previous",
                comparison,
                source="pay_comparison.compare_with_previous",
            )
    return context


__all__ = [
    "AIContext",
    "build_anomaly_context",
    "build_department_variance_context",
    "build_employee_context",
    "build_payroll_blockers_context",
    "build_payslip_explanation_context",
    "deterministic_signals",
]
