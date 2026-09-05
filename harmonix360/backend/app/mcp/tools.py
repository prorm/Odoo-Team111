"""The PeoplePay360 MCP tool set (Architecture §8.2).

Every function here is a thin adapter: resolve the actor, apply §5's matrix
through the router's own `require_role`, call an EXISTING service method, and
serialize the result with money as strings. There is no business logic in this
file. Grep it for `select(` and you will find only the two places a tool needs
to look up the row it was given a public id for; grep it for arithmetic on an
amount and you will find none.

WHY THE BODIES LIVE HERE AND NOT IN server.py
---------------------------------------------
`server.py` registers these with FastMCP, which means they normally run inside a
separate process behind a Streamable HTTP transport. Keeping the bodies as plain
async functions that take a session makes them callable three ways with
identical behaviour:

    * the MCP server process, via `@mcp_tool(mcp)`;
    * the AI orchestration layer, for progressive investigation;
    * the test suite, which asserts that an MCP mutation and the REST route
      produce the SAME validation error from the SAME service (§9's
      single-path-to-the-database property).

That third one is the reason this shape matters. If the tool bodies only existed
inside decorators in a process the tests do not start, "MCP uses the same service
as REST" would be a claim in a docstring rather than something a test can fail.

READ TOOLS ARE AUTHORIZED TOO
-----------------------------
§5 is not only about mutations: HR Manager has zero payroll access, and an
Employee may read their own records and nobody else's. A read tool that skipped
the check would be an information-disclosure path that bypassed RBAC, which §9
forbids just as firmly as a write path would be.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import encode_public_id
from app.models.attendance import Attendance
from app.models.enums import HR_ROLES, PAYROLL_ROLES, TimeOffRequestStatus
from app.models.time_off import TimeOffAllocation, TimeOffRequest
from app.mcp.identity import actor_with_role, resolve_actor
from app.repositories.hr import EmployeeRepository
from app.schemas.attendance import AttendanceCorrection
from app.schemas.payroll import PayrunCreate
from app.schemas.time_off import Decision, RequestCreate
from app.services import contract_history, pay_comparison, payslip_explain
from app.services.attendance import AttendanceService, schedule_expectations
from app.services.dashboard import DashboardService, resolve_filters
from app.services.employee import EmployeeService
from app.services.payroll import PayrunService, PayslipService
from app.services.payroll_context import attendance_facts, period_days
from app.services.payslip_snapshot import payslip_response
from app.services.time_off import TimeOffRequestService

#: Every read tool that touches payroll money. HR Manager is deliberately absent
#: — Architecture §5 gives that role full HR rights and zero payroll rights, and
#: an agent acting as an HR Manager must hit the same wall a browser would.
_PAYROLL_READ = PAYROLL_ROLES


async def _employee_or_404(session: AsyncSession, employee_id: str):
    employee = await EmployeeRepository(session).get_by_public_id(employee_id or "")
    if employee is None:
        raise HTTPException(404, f"Employee '{employee_id}' not found")
    return employee


def _money(value) -> Optional[str]:
    return None if value is None else str(value)


def _allocation_public_id(request) -> Optional[str]:
    """`TimeOffRequest.allocation_id` is an internal FK; the wire carries the
    hashid. Encoded the same way `app/api/v1/routers/time_off.py` encodes it, so
    an id from an MCP response can be pasted straight into a REST call."""
    return encode_public_id(request.allocation_id, "alloc") if request.allocation_id else None


# ==========================================================================
# READ TOOLS
# ==========================================================================


async def get_employee(session: AsyncSession, employee_id: str, actor_email: str) -> dict:
    """Identity, department, manager and employment dates for one employee."""
    user = await resolve_actor(session, actor_email)
    employee = await _employee_or_404(session, employee_id)
    # The service's own rule, including its deliberate 404-not-403 on another
    # employee's record so ids cannot be enumerated.
    EmployeeService(session).assert_can_read(user, employee)
    counts = await EmployeeService(session).smart_button_counts(employee)
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
        "has_bank_details": bool(employee.bank_account),
        "related_counts": counts.model_dump(),
    }


async def get_employee_contracts(
    session: AsyncSession, employee_id: str, actor_email: str
) -> dict:
    """Full contract history with the wage changes between consecutive
    contracts — the Time Machine's underlying data."""
    user = await resolve_actor(session, actor_email)
    employee = await _employee_or_404(session, employee_id)
    EmployeeService(session).assert_can_read(user, employee)
    return await contract_history.contract_timeline(session, employee)


async def get_attendance_summary(
    session: AsyncSession,
    employee_id: str,
    period_start: str,
    period_end: str,
    actor_email: str,
) -> dict:
    """Worked days, scheduled days and missing checkouts over a period.

    `worked_days` is produced by `payroll_context.attendance_facts` — the same
    function the payroll engine calls — so this tool and a payslip can never
    report different day counts for the same period.
    """
    user = await resolve_actor(session, actor_email)
    employee = await _employee_or_404(session, employee_id)
    EmployeeService(session).assert_can_read(user, employee)

    start, end = date.fromisoformat(period_start), date.fromisoformat(period_end)
    facts = await attendance_facts(session, employee, start, end)

    # `schedule_expectations` returns (expected_start, net_hours); a day the
    # schedule does not cover gives net_hours 0, and a missing/deleted schedule
    # gives None — which is a different fact and must not count as a working day.
    scheduled = 0
    for day in period_days(start, end):
        _, net_hours = schedule_expectations(employee, day)
        if net_hours is not None and net_hours > 0:
            scheduled += 1

    rows = (
        (
            await session.execute(
                select(Attendance)
                .where(
                    Attendance.employee_id == employee.id,
                    Attendance.deleted_at.is_(None),
                    Attendance.check_in >= datetime.combine(start, datetime.min.time()),
                    Attendance.check_in <= datetime.combine(end, datetime.max.time()),
                )
                .order_by(Attendance.check_in)
            )
        )
        .scalars()
        .all()
    )
    by_status: dict[str, int] = {}
    for row in rows:
        by_status[row.status.value] = by_status.get(row.status.value, 0) + 1

    return {
        "employee_id": employee.public_id,
        "employee_name": employee.full_name,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "worked_days": str(facts.worked_days),
        "scheduled_working_days": scheduled,
        "attendance_record_count": len(rows),
        "records_by_status": by_status,
        "missing_checkout_dates": [d.isoformat() for d in facts.missing_checkout_dates],
        "worked_dates": [d.isoformat() for d in facts.worked_dates],
    }


async def get_leave_balance(
    session: AsyncSession, employee_id: str, actor_email: str
) -> dict:
    """Allocations with allocated / taken / remaining, per leave type."""
    user = await resolve_actor(session, actor_email)
    employee = await _employee_or_404(session, employee_id)
    EmployeeService(session).assert_can_read(user, employee)

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
    return {
        "employee_id": employee.public_id,
        "employee_name": employee.full_name,
        "allocations": [
            {
                "allocation_id": row.public_id,
                "type": row.time_off_type.name,
                "type_code": row.time_off_type.code,
                "unit": row.time_off_type.unit.value,
                "affects_payroll": bool(row.time_off_type.payroll_integration),
                "allocated": str(row.allocated),
                "taken": str(row.taken),
                "remaining": str(row.allocated - row.taken),
                "valid_from": row.valid_from.isoformat(),
                "valid_to": row.valid_to.isoformat() if row.valid_to else None,
                "status": row.status.value,
                "version": row.version,
            }
            for row in rows
        ],
    }


async def get_pending_time_off(
    session: AsyncSession, actor_email: str, employee_id: str = "", limit: int = 25
) -> dict:
    """Time-off requests awaiting a decision — the approver's worklist."""
    user = await resolve_actor(session, actor_email)
    conditions = [
        TimeOffRequest.deleted_at.is_(None),
        TimeOffRequest.status == TimeOffRequestStatus.TO_APPROVE,
    ]
    if employee_id:
        employee = await _employee_or_404(session, employee_id)
        EmployeeService(session).assert_can_read(user, employee)
        conditions.append(TimeOffRequest.employee_id == employee.id)
    elif not user.is_hr():
        # An Employee has no approval queue. Scope them to their own pending
        # requests rather than refusing outright — the question "what have I got
        # outstanding?" is legitimate and §5 grants it.
        if not user.employee_public_id:
            raise HTTPException(404, "No employee is linked to this login")
        own = await _employee_or_404(session, user.employee_public_id)
        conditions.append(TimeOffRequest.employee_id == own.id)

    rows = (
        (
            await session.execute(
                select(TimeOffRequest)
                .where(*conditions)
                .order_by(TimeOffRequest.date_from)
                .limit(max(1, min(limit, 100)))
            )
        )
        .scalars()
        .all()
    )
    return {
        "count": len(rows),
        "requests": [
            {
                "request_id": row.public_id,
                "employee_id": row.employee.public_id,
                "employee_name": row.employee.full_name,
                "type": row.time_off_type.name,
                "type_code": row.time_off_type.code,
                "affects_payroll": bool(row.time_off_type.payroll_integration),
                "date_from": row.date_from.isoformat(),
                "date_to": row.date_to.isoformat(),
                "duration": str(row.duration),
                "status": row.status.value,
                "version": row.version,
            }
            for row in rows
        ],
    }


async def get_payrun_summary(
    session: AsyncSession, payrun_id: str, actor_email: str
) -> dict:
    """A payrun's status, selection, totals and its validation report."""
    await actor_with_role(session, actor_email, _PAYROLL_READ)
    service = PayrunService(session)
    payrun = await service.read_payrun(payrun_id)
    counts = await service.payslip_counts([payrun])
    report = await service.validation_report(payrun)
    from app.ai.context_builder import _stringify

    return {
        "payrun_id": payrun.public_id,
        "name": payrun.name,
        "period_start": payrun.period_start.isoformat(),
        "period_end": payrun.period_end.isoformat(),
        "status": payrun.status.value,
        "version": payrun.version,
        "salary_structure": {
            "code": payrun.salary_structure.code,
            "name": payrun.salary_structure.name,
        },
        "selected_employee_count": len(payrun.selected_employees),
        "payslip_count": counts.get(payrun.id, 0),
        "validation_report": _stringify(report),
    }


async def get_payslip(session: AsyncSession, payslip_id: str, actor_email: str) -> dict:
    """One payslip, through the shared historical-snapshot boundary.

    Uses `payslip_snapshot.payslip_response`, the same serializer the REST
    detail route and the PDF worker use — so a legacy row with no snapshot
    answers 409 here exactly as it does everywhere else, rather than being
    quietly reconstructed from today's contract.
    """
    await actor_with_role(session, actor_email, _PAYROLL_READ)
    payslip = await PayslipService(session).read_payslip(payslip_id)
    return payslip_response(payslip).model_dump(mode="json")


async def explain_payslip(
    session: AsyncSession, payslip_id: str, actor_email: str
) -> dict:
    """The deterministic calculation tree: frozen inputs, the lines in the
    sequence they ran, category subtotals, and the persisted totals.

    Read-only and arithmetic-free. This is the data an explanation is built
    FROM; the narration itself happens in the AI layer, which may not change a
    single figure here.
    """
    await actor_with_role(session, actor_email, _PAYROLL_READ)
    payslip = await PayslipService(session).read_payslip(payslip_id)
    tree = await payslip_explain.calculation_tree(session, payslip)
    tree["comparison_with_previous_period"] = await pay_comparison.compare_with_previous(
        session, payslip
    )
    return tree


async def get_payroll_warnings(
    session: AsyncSession,
    actor_email: str,
    payrun_id: str = "",
    period_start: str = "",
    period_end: str = "",
) -> dict:
    """Deterministic payroll findings — a payrun's validation report when a run
    is named, otherwise the dashboard's period-scoped warning list."""
    await actor_with_role(session, actor_email, _PAYROLL_READ)
    from app.ai.context_builder import _stringify

    if payrun_id:
        service = PayrunService(session)
        payrun = await service.read_payrun(payrun_id)
        return {
            "scope": "payrun",
            "payrun_id": payrun.public_id,
            "report": _stringify(await service.validation_report(payrun)),
        }

    filters = await resolve_filters(
        session,
        period_start=date.fromisoformat(period_start) if period_start else None,
        period_end=date.fromisoformat(period_end) if period_end else None,
        department_id=None,
        employee_type=None,
    )
    warnings = await DashboardService(session).payroll_warnings(filters)
    return {
        "scope": "period",
        "period_start": filters.period_start.isoformat(),
        "period_end": filters.period_end.isoformat(),
        "warning_count": len(warnings),
        "warnings": _stringify(warnings),
    }


async def get_department_payroll(
    session: AsyncSession,
    actor_email: str,
    department_id: str = "",
    period_start: str = "",
    period_end: str = "",
) -> dict:
    """Department-level payroll aggregates for a period, from the same queries
    the Reports screen renders."""
    await actor_with_role(session, actor_email, _PAYROLL_READ)
    filters = await resolve_filters(
        session,
        period_start=date.fromisoformat(period_start) if period_start else None,
        period_end=date.fromisoformat(period_end) if period_end else None,
        department_id=department_id or None,
        employee_type=None,
    )
    service = DashboardService(session)
    from app.ai.context_builder import _stringify

    return {
        "period_start": filters.period_start.isoformat(),
        "period_end": filters.period_end.isoformat(),
        "department_id": filters.department_public_id,
        "total_net_salary_paid": str(await service.total_net_salary_paid(filters)),
        "payslips_generated": await service.payslips_generated(filters),
        "average_salary": str(await service.average_salary(filters)),
        "salary_cost_by_department": _stringify(
            await service.salary_cost_by_department(filters)
        ),
        "department_breakdown": _stringify(await service.department_breakdown(filters)),
    }


async def get_payroll_trends(
    session: AsyncSession,
    actor_email: str,
    employee_id: str = "",
    department_id: str = "",
) -> dict:
    """Net pay over time — one employee's payslip series, or the organisation's
    monthly paid trend."""
    await actor_with_role(session, actor_email, _PAYROLL_READ)
    if employee_id:
        employee = await _employee_or_404(session, employee_id)
        return {
            "scope": "employee",
            "employee_id": employee.public_id,
            "employee_name": employee.full_name,
            "series": await pay_comparison.payroll_trend(session, employee),
        }

    filters = await resolve_filters(
        session,
        period_start=None,
        period_end=None,
        department_id=department_id or None,
        employee_type=None,
    )
    trend = await DashboardService(session).monthly_net_salary_trend(filters)
    return {
        "scope": "organisation",
        "department_id": filters.department_public_id,
        "series": [{"month": row["month"], "amount": str(row["amount"])} for row in trend],
    }


async def find_payroll_anomalies(
    session: AsyncSession,
    actor_email: str,
    period_start: str = "",
    period_end: str = "",
    department_id: str = "",
) -> dict:
    """Deterministic anomaly signals. Application checks, never a model's
    opinion (PRD §5.7)."""
    await actor_with_role(session, actor_email, _PAYROLL_READ)
    from app.ai.context_builder import deterministic_signals

    filters = await resolve_filters(
        session,
        period_start=date.fromisoformat(period_start) if period_start else None,
        period_end=date.fromisoformat(period_end) if period_end else None,
        department_id=department_id or None,
        employee_type=None,
    )
    found = await deterministic_signals(session, filters)
    return {
        "period_start": filters.period_start.isoformat(),
        "period_end": filters.period_end.isoformat(),
        "source": found["source"],
        "count": len(found["signals"]),
        "by_severity": found["by_severity"],
        "by_type": found["by_type"],
        "signals": found["signals"],
    }


async def find_contract_conflicts(
    session: AsyncSession, actor_email: str, employee_id: str = ""
) -> dict:
    """Overlapping active contracts. Should always be empty — a non-empty
    result means the EXCLUDE constraint is gone."""
    await actor_with_role(session, actor_email, HR_ROLES)
    employee = await _employee_or_404(session, employee_id) if employee_id else None
    conflicts = await contract_history.find_contract_conflicts(session, employee=employee)
    return {
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "note": (
            "Zero is the expected result: contracts_active_period_overlap_excl makes an "
            "overlap impossible to insert. A non-zero count means that constraint is no "
            "longer in place."
        ),
    }


# ==========================================================================
# CONTROLLED ACTION TOOLS
# ==========================================================================
#
# Each one: resolve the actor -> apply §5 -> call the SAME service method the
# REST router calls -> let that service do its own validation and write its own
# audit row. None of them opens a transaction, writes a column, or checks a
# business rule itself. The `@mcp_tool` wrapper owns commit/rollback.


async def create_time_off_request(
    session: AsyncSession,
    employee_id: str,
    time_off_type_id: str,
    date_from: str,
    date_to: str,
    actor_email: str,
    reason: str = "",
) -> dict:
    """Submit a time-off request through `TimeOffRequestService.create_request`.

    No role list is applied here on purpose. §5 grants "Time Off Requests (own
    create)" to the Employee role and full rights to HR, and that distinction is
    enforced inside the service by `employee_for` — which compares against the
    actor's own employee link, not against anything the caller sent. Adding a
    `require_role` here would either duplicate that rule or contradict it; the
    REST route makes exactly the same choice, and this tool matches it so both
    entry points fail identically.
    """
    user = await resolve_actor(session, actor_email)
    row = await TimeOffRequestService(session).create_request(
        RequestCreate(
            employee_id=employee_id,
            time_off_type_id=time_off_type_id,
            date_from=date.fromisoformat(date_from),
            date_to=date.fromisoformat(date_to),
            reason=reason or None,
        ),
        user,
    )
    return {
        "request_id": row.public_id,
        "employee_id": row.employee.public_id,
        "employee_name": row.employee.full_name,
        "type": row.time_off_type.name,
        "date_from": row.date_from.isoformat(),
        "date_to": row.date_to.isoformat(),
        "duration": str(row.duration),
        "status": row.status.value,
        "version": row.version,
        "allocation_id": _allocation_public_id(row),
    }


async def approve_time_off_request(
    session: AsyncSession,
    request_id: str,
    version: int,
    actor_email: str,
    approve: bool = True,
    decision_note: str = "",
) -> dict:
    """Approve or refuse through `TimeOffRequestService.decide`.

    `require_hr` is that method's first statement, before any read — so an
    Employee acting through MCP is refused before the request is even loaded,
    exactly as through REST. `version` is required for the same reason the REST
    route requires it: two approvers deciding the same request must not both
    win.
    """
    user = await resolve_actor(session, actor_email)
    row = await TimeOffRequestService(session).decide(
        request_id,
        Decision(version=version, decision_note=decision_note or None),
        user,
        approve=approve,
    )
    return {
        "request_id": row.public_id,
        "status": row.status.value,
        "version": row.version,
        "decision_note": row.decision_note,
        "allocation_id": _allocation_public_id(row),
    }


async def correct_attendance(
    session: AsyncSession,
    attendance_id: str,
    version: int,
    check_in: str,
    correction_reason: str,
    actor_email: str,
    check_out: str = "",
) -> dict:
    """Correct an attendance record through `AttendanceService.correct`.

    Corrections are HR-only and online-only — Architecture §8.3 keeps them out
    of the offline sync registry for the same reason. The service demands a
    non-empty reason and writes it to the audit row; this tool does not
    supply a default one, because "corrected via AI" is not a reason.
    """
    user = await resolve_actor(session, actor_email)
    row = await AttendanceService(session).correct(
        attendance_id,
        AttendanceCorrection(
            version=version,
            check_in=datetime.fromisoformat(check_in),
            check_out=datetime.fromisoformat(check_out) if check_out else None,
            correction_reason=correction_reason,
        ),
        user,
    )
    return {
        "attendance_id": row.public_id,
        "employee_id": row.employee.public_id,
        "check_in": row.check_in.isoformat(),
        "check_out": row.check_out.isoformat() if row.check_out else None,
        "worked_hours": _money(row.worked_hours),
        "status": row.status.value,
        "correction_reason": row.correction_reason,
        "version": row.version,
        "note": (
            "Correcting attendance does not change an already-computed payslip. "
            "The payrun must be recomputed for this to affect payroll."
        ),
    }


async def create_payrun(
    session: AsyncSession,
    name: str,
    salary_structure_id: str,
    period_start: str,
    period_end: str,
    employee_ids: list[str],
    actor_email: str,
) -> dict:
    """Create a payrun through `PayrunService.create_payrun`.

    Creates only. There is deliberately no `compute` tool: Compute is the write
    path to `Payslip`/`PayslipLine`, and Architecture §7 states the AI layer has
    no code path that reaches it. An agent can prepare a run and a human presses
    Compute — which is also where the `Idempotency-Key` the REST route requires
    is supplied by the client that will retry.
    """
    user = await actor_with_role(session, actor_email, PAYROLL_ROLES)
    payrun = await PayrunService(session).create_payrun(
        PayrunCreate(
            name=name,
            salary_structure_id=salary_structure_id,
            period_start=date.fromisoformat(period_start),
            period_end=date.fromisoformat(period_end),
            employee_ids=list(employee_ids),
        ),
        actor_email=user.email,
    )
    return {
        "payrun_id": payrun.public_id,
        "name": payrun.name,
        "period_start": payrun.period_start.isoformat(),
        "period_end": payrun.period_end.isoformat(),
        "status": payrun.status.value,
        "version": payrun.version,
        "selected_employee_count": len(payrun.selected_employees),
        "next_step": (
            "A human must run Compute on this payrun. No AI or MCP path writes "
            "Payslip or PayslipLine rows."
        ),
    }


async def request_payroll_validation(
    session: AsyncSession, payrun_id: str, version: int, actor_email: str
) -> dict:
    """Run the validation firewall through `PayrunService.validate_payrun`.

    This is the "Revalidate" action, not a new engine: it re-derives every
    finding from persisted state and refuses with 409 while a blocking issue
    stands, identically to the button.
    """
    user = await actor_with_role(session, actor_email, PAYROLL_ROLES)
    from app.ai.context_builder import _stringify

    report = await PayrunService(session).validate_payrun(
        payrun_id, version, actor_email=user.email
    )
    return _stringify(report)


#: Names exposed to an agent, split by whether they can change anything.
#: `find_payroll_anomalies` and friends are reads even though they sound
#: analytical — nothing in the read set opens a write path.
READ_TOOLS = (
    get_employee,
    get_employee_contracts,
    get_attendance_summary,
    get_leave_balance,
    get_pending_time_off,
    get_payrun_summary,
    get_payslip,
    explain_payslip,
    get_payroll_warnings,
    get_department_payroll,
    get_payroll_trends,
    find_payroll_anomalies,
    find_contract_conflicts,
)

ACTION_TOOLS = (
    create_time_off_request,
    approve_time_off_request,
    correct_attendance,
    create_payrun,
    request_payroll_validation,
)

__all__ = [tool.__name__ for tool in READ_TOOLS + ACTION_TOOLS] + [
    "ACTION_TOOLS",
    "READ_TOOLS",
]
