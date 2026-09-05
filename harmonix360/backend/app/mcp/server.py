"""
FastMCP server exposing PeoplePay360 capabilities as agent tools.

Architecture ref: §8.2 — FastMCP, Streamable HTTP transport, run as a separate
process from the main FastAPI app. Library is FastMCP (decorator-based), NOT
the low-level mcp SDK and NOT fastapi_mcp. Tools are hand-written, never
auto-converted from the OpenAPI spec via `FastMCP.from_fastapi()`.

STATUS: DORMANT UNTIL PHASE 9.
--------------------------------
The server, its auth model, the @mcp_tool plumbing (app/mcp/tool_wrapper.py)
and its Compose service are all retained — they are Platform/Intelligence
layer, not domain content. What was removed in Phase 0 step 2 is the AssetFlow
TOOL SET, which called services that no longer exist:

    create_booking, check_asset_status, approve_transfer, create_note

`query_audit_trail` survives unchanged: it reads the domain-agnostic audit log
through AuditQueryRepository and named no deleted entity, so it is also the
one live proof that the transport, the API-key check, the session/commit
wrapper and the audit write still work end to end with zero domain entities
registered.

Phase 9 adds the HR/Payroll tool set from Architecture §8.2:

  Read tools:   get_employee, get_employee_contracts, get_attendance_summary,
                get_leave_balance, get_pending_time_off, get_payrun_summary,
                get_payslip, explain_payslip, get_payroll_warnings,
                get_department_payroll, get_payroll_trends,
                find_payroll_anomalies, find_contract_conflicts
  Action tools: create_time_off_request, approve_time_off_request,
                correct_attendance, create_payrun, request_payroll_validation

Every one of those must import and call the same `app/services/*` method the
REST router calls — Architecture §9, "one path to the database". No tool may
execute raw SQL, reimplement contract-overlap or allocation-deduction logic,
or bypass RBAC, idempotency or audit. Mutating tools authorize against
Architecture §5's matrix bound to the authenticated user's role, not to a
looser agent-wide scope, and audit with actor="ai-agent" (or the impersonated
user).

Auth: MCP_AGENT_API_KEY validated per request by @mcp_tool, separate from the
user JWT the REST API uses.
"""
import os
import sys
import logging

# Ensure the backend app is importable when this module is run as a process.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastmcp import FastMCP
from app.core.config import settings
from app.repositories.audit_query import AuditQueryRepository
from app.audit.logger import AuditLogger
from app.mcp.tool_wrapper import mcp_tool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("harmonix360.mcp")

mcp = FastMCP(
    "PeoplePay360 MCP Server",
    instructions=(
        "PeoplePay360 HR & Payroll tools. Read tools answer questions about employees, "
        "contracts, attendance, leave and payroll; action tools submit and approve "
        "requests through the same authorized services a human uses. The HR/Payroll "
        "tool set lands in Phase 9; only the audit-trail reader is exposed today."
    ),
)


@mcp_tool(mcp)
async def query_audit_trail(
    session,
    entity_public_id: str,
    limit: int = 20,
    api_key: str = "",
) -> list[dict]:
    """
    Query the immutable audit trail for a specific entity.

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


if __name__ == "__main__":
    port = int(os.environ.get("MCP_SERVER_PORT", settings.MCP_SERVER_PORT))
    logger.info("Starting PeoplePay360 MCP Server on port %d (Streamable HTTP)", port)
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
