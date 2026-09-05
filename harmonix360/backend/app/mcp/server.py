"""FastMCP server exposing PeoplePay360 capabilities as agent tools.

Architecture ref: §8.2 — FastMCP, Streamable HTTP transport, run as a separate
process from the main FastAPI app. Library is FastMCP (decorator-based), NOT
the low-level mcp SDK and NOT fastapi_mcp. Tools are hand-written, never
auto-converted from the OpenAPI spec via `FastMCP.from_fastapi()`.

STATUS: LIVE AS OF PHASE 9.
---------------------------
Every function below is a registration shim. It declares the tool's public
schema — including `api_key`, which `@mcp_tool` validates — and immediately
delegates to `app/mcp/tools.py`, where the body calls an existing service.
Nothing in this file makes a decision, reads a table, or touches an amount.

That split is deliberate. It keeps the tool bodies importable and testable
without starting this process, so the claim "MCP and REST share one path to the
database" (Architecture §9) is proven by a test that calls both, rather than
asserted in a comment. It also means the docstrings here — which are what the
model actually reads when choosing a tool — can be written for the model, while
the reasoning about correctness lives next to the code it constrains.

AUTHORIZATION IS TWO SEPARATE CHECKS
------------------------------------
`api_key` (MCP_AGENT_API_KEY) authenticates the PROCESS: may this client talk to
this server at all. `actor_email` identifies the PERSON the agent is acting for,
and Architecture §5's matrix is applied to that person's role by the same
`require_role` the REST routers use. A valid key never widens what the actor may
do: an agent acting as an HR Manager gets 403 on payroll exactly as that user's
browser would.

`create_payrun` exists; `compute` deliberately does not. Compute is the only
write path to `Payslip`/`PayslipLine`, and Architecture §7 states that no AI code
path reaches the rule engine's write methods. An agent can prepare a run; a human
presses Compute.
"""
import logging
import os
import sys

# Ensure the backend app is importable when this module is run as a process.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastmcp import FastMCP  # noqa: E402

from app.audit.logger import AuditLogger  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.mcp import tools  # noqa: E402
from app.mcp.tool_wrapper import mcp_tool  # noqa: E402
from app.repositories.audit_query import AuditQueryRepository  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("harmonix360.mcp")

mcp = FastMCP(
    "PeoplePay360 MCP Server",
    instructions=(
        "PeoplePay360 HR & Payroll tools.\n\n"
        "Read tools answer questions about employees, contracts, attendance, leave and "
        "payroll. Investigate progressively: start from the entity the question names, "
        "then follow the links that matter (a pay change usually means checking the "
        "previous payslip, then the contract history, then attendance and unpaid leave).\n\n"
        "Every figure these tools return was calculated by the deterministic payroll "
        "engine. Report those figures exactly; never recalculate a payslip and never "
        "state an amount a tool did not return.\n\n"
        "Action tools change real records through the same validated, audited services a "
        "human uses. Never call one to satisfy an inferred intent: propose the action, "
        "get an explicit human confirmation, and only then call it. Every call must name "
        "the user it acts for in `actor_email`; that user's role decides what is allowed."
    ),
)


# ==========================================================================
# READ TOOLS (Architecture §8.2)
# ==========================================================================


@mcp_tool(mcp)
async def get_employee(session, employee_id: str, actor_email: str, api_key: str = "") -> dict:
    """Look up one employee: identity, department, manager, employment dates,
    whether bank details are on file, and counts of their related records.

    Args:
        employee_id: The employee's public id (emp_...).
        actor_email: The PeoplePay360 user this call acts for.
        api_key: MCP agent API key.
    """
    return await tools.get_employee(session, employee_id, actor_email)


@mcp_tool(mcp)
async def get_employee_contracts(session, employee_id: str, actor_email: str, api_key: str = "") -> dict:
    """Full contract history for an employee, oldest first, with the wage
    change between each pair of consecutive contracts.

    Use this to find out whether a pay change was caused by a contract change.

    Args:
        employee_id: The employee's public id (emp_...).
        actor_email: The PeoplePay360 user this call acts for.
        api_key: MCP agent API key.
    """
    return await tools.get_employee_contracts(session, employee_id, actor_email)


@mcp_tool(mcp)
async def get_attendance_summary(
    session,
    employee_id: str,
    period_start: str,
    period_end: str,
    actor_email: str,
    api_key: str = "",
) -> dict:
    """Worked days, scheduled working days, missing checkouts and per-status
    record counts for one employee over one period.

    `worked_days` here is the same figure the payroll engine used.

    Args:
        employee_id: The employee's public id (emp_...).
        period_start: Inclusive ISO date, e.g. 2026-08-01.
        period_end: Inclusive ISO date, e.g. 2026-08-31.
        actor_email: The PeoplePay360 user this call acts for.
        api_key: MCP agent API key.
    """
    return await tools.get_attendance_summary(
        session, employee_id, period_start, period_end, actor_email
    )


@mcp_tool(mcp)
async def get_leave_balance(session, employee_id: str, actor_email: str, api_key: str = "") -> dict:
    """Leave allocations for one employee: allocated, taken and remaining per
    type, with whether each type affects payroll.

    Check this before proposing a leave request.

    Args:
        employee_id: The employee's public id (emp_...).
        actor_email: The PeoplePay360 user this call acts for.
        api_key: MCP agent API key.
    """
    return await tools.get_leave_balance(session, employee_id, actor_email)


@mcp_tool(mcp)
async def get_pending_time_off(
    session, actor_email: str, employee_id: str = "", limit: int = 25, api_key: str = ""
) -> dict:
    """Time-off requests awaiting a decision. Omit employee_id for the whole
    approval queue; an Employee actor sees only their own.

    Args:
        actor_email: The PeoplePay360 user this call acts for.
        employee_id: Optional employee public id to filter by.
        limit: Maximum requests to return (1-100).
        api_key: MCP agent API key.
    """
    return await tools.get_pending_time_off(session, actor_email, employee_id, limit)


@mcp_tool(mcp)
async def get_payrun_summary(session, payrun_id: str, actor_email: str, api_key: str = "") -> dict:
    """A payrun's status, period, structure, selection size, payslip count and
    full validation report (blocking and advisory findings).

    Args:
        payrun_id: The payrun's public id (prun_...).
        actor_email: The PeoplePay360 user this call acts for.
        api_key: MCP agent API key.
    """
    return await tools.get_payrun_summary(session, payrun_id, actor_email)


@mcp_tool(mcp)
async def get_payslip(session, payslip_id: str, actor_email: str, api_key: str = "") -> dict:
    """One payslip as persisted: employee, contract, worked days, gross, net,
    every line, and its warnings. All amounts are exact decimal strings.

    Args:
        payslip_id: The payslip's public id (pslip_...).
        actor_email: The PeoplePay360 user this call acts for.
        api_key: MCP agent API key.
    """
    return await tools.get_payslip(session, payslip_id, actor_email)


@mcp_tool(mcp)
async def explain_payslip(session, payslip_id: str, actor_email: str, api_key: str = "") -> dict:
    """The calculation tree behind a payslip: the inputs frozen at compute
    time, every rule in the sequence it ran, category subtotals, the persisted
    totals, and a comparison against the employee's previous period.

    This is the data to explain a payslip FROM. Do not recompute anything in it.

    Args:
        payslip_id: The payslip's public id (pslip_...).
        actor_email: The PeoplePay360 user this call acts for.
        api_key: MCP agent API key.
    """
    return await tools.explain_payslip(session, payslip_id, actor_email)


@mcp_tool(mcp)
async def get_payroll_warnings(
    session,
    actor_email: str,
    payrun_id: str = "",
    period_start: str = "",
    period_end: str = "",
    api_key: str = "",
) -> dict:
    """Deterministic payroll findings. Give a payrun_id for that run's
    validation report, or a period for all warnings raised in it.

    Blocking findings stop a payrun being validated; advisory ones do not.

    Args:
        actor_email: The PeoplePay360 user this call acts for.
        payrun_id: Optional payrun public id (prun_...).
        period_start: Optional inclusive ISO date.
        period_end: Optional inclusive ISO date.
        api_key: MCP agent API key.
    """
    return await tools.get_payroll_warnings(
        session, actor_email, payrun_id, period_start, period_end
    )


@mcp_tool(mcp)
async def get_department_payroll(
    session,
    actor_email: str,
    department_id: str = "",
    period_start: str = "",
    period_end: str = "",
    api_key: str = "",
) -> dict:
    """Department payroll aggregates for a period: total net paid, payslip
    count, average salary, cost by department and headcount breakdown.

    Args:
        actor_email: The PeoplePay360 user this call acts for.
        department_id: Optional department public id (dept_...).
        period_start: Optional inclusive ISO date.
        period_end: Optional inclusive ISO date.
        api_key: MCP agent API key.
    """
    return await tools.get_department_payroll(
        session, actor_email, department_id, period_start, period_end
    )


@mcp_tool(mcp)
async def get_payroll_trends(
    session, actor_email: str, employee_id: str = "", department_id: str = "", api_key: str = ""
) -> dict:
    """Net pay over time. With employee_id, that employee's payslip series;
    otherwise the organisation's monthly paid trend.

    Args:
        actor_email: The PeoplePay360 user this call acts for.
        employee_id: Optional employee public id (emp_...).
        department_id: Optional department public id, for the org-level trend.
        api_key: MCP agent API key.
    """
    return await tools.get_payroll_trends(session, actor_email, employee_id, department_id)


@mcp_tool(mcp)
async def find_payroll_anomalies(
    session,
    actor_email: str,
    period_start: str = "",
    period_end: str = "",
    department_id: str = "",
    api_key: str = "",
) -> dict:
    """Anomalies found by the application's own deterministic checks — large
    salary jumps, missing bank details, missing checkouts, low attendance,
    expiring contracts, department spend spikes.

    These are query results, not opinions. Explain them; do not add to them.

    Args:
        actor_email: The PeoplePay360 user this call acts for.
        period_start: Optional inclusive ISO date.
        period_end: Optional inclusive ISO date.
        department_id: Optional department public id (dept_...).
        api_key: MCP agent API key.
    """
    return await tools.find_payroll_anomalies(
        session, actor_email, period_start, period_end, department_id
    )


@mcp_tool(mcp)
async def find_contract_conflicts(
    session, actor_email: str, employee_id: str = "", api_key: str = ""
) -> dict:
    """Active contracts with overlapping date ranges. Expected to be empty —
    a database constraint makes overlaps impossible to insert.

    Args:
        actor_email: The PeoplePay360 user this call acts for.
        employee_id: Optional employee public id to check just one person.
        api_key: MCP agent API key.
    """
    return await tools.find_contract_conflicts(session, actor_email, employee_id)


@mcp_tool(mcp)
async def query_audit_trail(
    session, entity_public_id: str, limit: int = 20, api_key: str = ""
) -> list[dict]:
    """Query the immutable audit trail for a specific entity.

    Args:
        entity_public_id: The public ID of the entity to query audit logs for.
        limit: Maximum number of audit entries to return (default 20).
        api_key: MCP agent API key for authentication.

    Returns:
        List of audit log entries with actor, action, entity, diffs, and timestamp.
    """
    repo = AuditQueryRepository(session)
    entries = await repo.get_by_entity_id(entity_public_id, limit=limit)

    # The query itself is audited — reading who was paid what is a privileged
    # action, and Architecture §11 gives an agent read no quieter a trail than
    # a human one.
    await AuditLogger.log_mutation(
        session=session,
        actor="ai-agent",
        action="MCP_QUERY_AUDIT_TRAIL",
        entity="AuditLog",
        entity_id=entity_public_id,
        after_diff={"limit": limit, "results_count": len(entries)},
    )

    return [
        {
            "public_id": e.public_id,
            "actor": e.actor,
            "action": e.action,
            "entity": e.entity,
            "entity_id": e.entity_id,
            "before_diff": e.before_diff,
            "after_diff": e.after_diff,
            "reason": e.reason,
            "timestamp": str(e.timestamp),
        }
        for e in entries
    ]


# ==========================================================================
# CONTROLLED ACTION TOOLS (Architecture §8.2)
# ==========================================================================
#
# Each delegates to the same service method the REST router calls. Validation,
# version checks, allocation deduction and the audit row all happen inside that
# service — there is no MCP-specific rule anywhere below.


@mcp_tool(mcp)
async def create_time_off_request(
    session,
    employee_id: str,
    time_off_type_id: str,
    date_from: str,
    date_to: str,
    actor_email: str,
    reason: str = "",
    api_key: str = "",
) -> dict:
    """Submit a time-off request. MUTATES DATA.

    Only call this after a human has explicitly confirmed the specific request.
    Check the leave balance first — an insufficient balance is rejected by the
    same validation a human would hit.

    Args:
        employee_id: The employee's public id (emp_...).
        time_off_type_id: The leave type's public id.
        date_from: Inclusive ISO start date.
        date_to: Inclusive ISO end date.
        actor_email: The PeoplePay360 user this call acts for.
        reason: Optional free-text reason.
        api_key: MCP agent API key.
    """
    return await tools.create_time_off_request(
        session, employee_id, time_off_type_id, date_from, date_to, actor_email, reason
    )


@mcp_tool(mcp)
async def approve_time_off_request(
    session,
    request_id: str,
    version: int,
    actor_email: str,
    approve: bool = True,
    decision_note: str = "",
    api_key: str = "",
) -> dict:
    """Approve or refuse a time-off request. MUTATES DATA. HR roles only.

    Approving deducts from the matching allocation. `version` must be the one
    read from the request; a stale version is refused so two approvers cannot
    both decide the same request.

    Args:
        request_id: The request's public id.
        version: The request's current version.
        actor_email: The PeoplePay360 user this call acts for.
        approve: True to approve, False to refuse.
        decision_note: Optional note recorded with the decision.
        api_key: MCP agent API key.
    """
    return await tools.approve_time_off_request(
        session, request_id, version, actor_email, approve, decision_note
    )


@mcp_tool(mcp)
async def correct_attendance(
    session,
    attendance_id: str,
    version: int,
    check_in: str,
    correction_reason: str,
    actor_email: str,
    check_out: str = "",
    api_key: str = "",
) -> dict:
    """Correct an attendance record. MUTATES DATA. HR roles only.

    A correction reason is required and is written to the audit trail.
    Correcting attendance does NOT change an already-computed payslip — the
    payrun must be recomputed by a human for payroll to reflect it.

    Args:
        attendance_id: The attendance record's public id.
        version: The record's current version.
        check_in: Corrected check-in as an ISO datetime with timezone.
        correction_reason: Why the correction is being made.
        actor_email: The PeoplePay360 user this call acts for.
        check_out: Optional corrected check-out as an ISO datetime.
        api_key: MCP agent API key.
    """
    return await tools.correct_attendance(
        session, attendance_id, version, check_in, correction_reason, actor_email, check_out
    )


@mcp_tool(mcp)
async def create_payrun(
    session,
    name: str,
    salary_structure_id: str,
    period_start: str,
    period_end: str,
    employee_ids: list[str],
    actor_email: str,
    api_key: str = "",
) -> dict:
    """Create a draft payrun. MUTATES DATA. Payroll roles only.

    Creates the run and its explicit employee selection. It does NOT compute
    payslips — there is no tool that does. A human must press Compute.

    Args:
        name: A name for the run.
        salary_structure_id: The salary structure's public id (sstr_...).
        period_start: Inclusive ISO start date.
        period_end: Inclusive ISO end date.
        employee_ids: Explicit list of employee public ids to include.
        actor_email: The PeoplePay360 user this call acts for.
        api_key: MCP agent API key.
    """
    return await tools.create_payrun(
        session, name, salary_structure_id, period_start, period_end, employee_ids, actor_email
    )


@mcp_tool(mcp)
async def request_payroll_validation(
    session, payrun_id: str, version: int, actor_email: str, api_key: str = ""
) -> dict:
    """Run the payroll validation firewall on a payrun. MUTATES STATE.
    Payroll roles only.

    This is the Revalidate action. It refuses while any blocking finding stands
    and reports every finding either way.

    Args:
        payrun_id: The payrun's public id (prun_...).
        version: The payrun's current version.
        actor_email: The PeoplePay360 user this call acts for.
        api_key: MCP agent API key.
    """
    return await tools.request_payroll_validation(session, payrun_id, version, actor_email)


if __name__ == "__main__":
    port = int(os.environ.get("MCP_SERVER_PORT", settings.MCP_SERVER_PORT))
    logger.info("Starting PeoplePay360 MCP Server on port %d (Streamable HTTP)", port)
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
