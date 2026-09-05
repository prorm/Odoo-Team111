"""
FastMCP server exposing Harmonix360 domain actions as tools.

Architecture ref: Section 6 — "Run FastMCP as a separate process from the main
FastAPI app using Streamable HTTP transport."

Library: FastMCP (pip install fastmcp) — decorator-based, NOT the low-level mcp SDK,
NOT fastapi_mcp.

Hand-written tools (NOT auto-generated from OpenAPI spec):
1. create_booking — create a resource booking
2. check_asset_status — check an asset's current status
3. approve_transfer — human override path for transfer approval
4. query_audit_trail — query the immutable audit log
5. create_note — trivial tool proving @mcp_tool needs no bespoke plumbing per tool

Auth: MCP_AGENT_API_KEY validated per request, NOT user JWT.
Every MCP tool call is audit-logged with actor="ai-agent" (Section 6).

The open-session / validate-key / commit-or-rollback / error-envelope shape that
used to be hand-repeated in every tool now lives in app/mcp/tool_wrapper.py's
@mcp_tool(mcp) decorator — each tool body below is entity-specific logic only.

NOTE: semantic_search_assets is NOT implemented yet — pgvector is Week 3.
"""
import os
import sys
import logging
from datetime import datetime

# Ensure the backend app is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastmcp import FastMCP
from app.core.config import settings
from app.services.booking import BookingService
from app.services.asset import AssetService
from app.services.transfer import TransferService
from app.services.note import NoteService
from app.repositories.audit_query import AuditQueryRepository
from app.audit.logger import AuditLogger
from app.mcp.tool_wrapper import mcp_tool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("harmonix360.mcp")

# Create FastMCP server instance
mcp = FastMCP(
    "Harmonix360 MCP Server",
    instructions="Harmonix360 platform tools. Use these to manage assets, bookings, transfers, notes, and audit trails.",
)


@mcp_tool(mcp)
async def create_booking(
    session,
    resource_type: str,
    resource_public_id: str,
    start_time: str,
    end_time: str,
    api_key: str = "",
) -> dict:
    """
    Create a resource booking for any registered resource type (e.g. "asset", "meeting_room").

    Args:
        resource_type: The registered resource type to book (see app/services/resource_registry.py).
        resource_public_id: The public ID (hashid) of the resource to book.
        start_time: ISO 8601 formatted start time (e.g. "2026-08-10T09:00:00Z").
        end_time: ISO 8601 formatted end time (e.g. "2026-08-10T17:00:00Z").
        api_key: MCP agent API key for authentication.

    Returns:
        dict with booking details including the booking public_id.
    """
    start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))

    service = BookingService(session)
    booking = await service.create_booking(
        resource_type=resource_type,
        resource_public_id=resource_public_id,
        start_time=start_dt,
        end_time=end_dt,
        actor_email="ai-agent",
    )

    return {
        "status": "success",
        "booking_id": booking.public_id,
        "resource_type": resource_type,
        "resource_public_id": resource_public_id,
        "start_time": str(start_dt),
        "end_time": str(end_dt),
        "booking_status": booking.status.value,
    }


@mcp_tool(mcp)
async def check_asset_status(
    session,
    asset_public_id: str,
    api_key: str = "",
) -> dict:
    """
    Check the current status and details of an asset.

    Args:
        asset_public_id: The public ID (hashid) of the asset to check.
        api_key: MCP agent API key for authentication.

    Returns:
        dict with asset name, status, condition, location, and bookable flag.
    """
    service = AssetService(session)
    asset = await service.get_asset(asset_public_id)

    # Audit log the status check with actor="ai-agent"
    await AuditLogger.log_mutation(
        session=session,
        actor="ai-agent",
        action="MCP_CHECK_ASSET_STATUS",
        entity="Asset",
        entity_id=asset_public_id,
        after_diff={"status": asset.status.value},
    )

    return {
        "asset_public_id": asset.public_id,
        "name": asset.name,
        "asset_tag": asset.asset_tag,
        "status": asset.status.value,
        "condition": asset.condition.value,
        "location": asset.location,
        "is_bookable": asset.is_bookable,
        "department_id": asset.department_id,
    }


@mcp_tool(mcp)
async def approve_transfer(
    session,
    transfer_public_id: str,
    note: str | None = None,
    api_key: str = "",
) -> dict:
    """
    Approve a transfer request (human override path via MCP).

    This calls the same override endpoint as the REST API — no parallel logic.
    The override is audit-logged with actor="ai-agent" (Section 6).

    Args:
        transfer_public_id: The public ID of the transfer request to approve.
        note: Optional note explaining the approval.
        api_key: MCP agent API key for authentication.

    Returns:
        dict with the updated transfer status.
    """
    service = TransferService(session)
    transfer = await service.human_override(
        transfer_public_id=transfer_public_id,
        override_decision="approve",
        note=note,
        actor_email="ai-agent",
    )

    return {
        "status": "success",
        "transfer_public_id": transfer.public_id,
        "transfer_status": transfer.status.value,
        "note": note,
    }


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

    # Audit log the query itself
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


@mcp_tool(mcp)
async def create_note(
    session,
    content: str,
    api_key: str = "",
) -> dict:
    """
    Create a note (proves @mcp_tool needs no bespoke session/audit/error-handling
    boilerplate per tool — this is the entirety of the entity-specific code).

    Args:
        content: The note's text content.
        api_key: MCP agent API key for authentication.

    Returns:
        dict with the created note's public_id and content.
    """
    note = await NoteService(session).create_note(content=content, actor_email="ai-agent")
    return {"status": "success", "note_id": note.public_id, "content": note.content}


if __name__ == "__main__":
    port = int(os.environ.get("MCP_SERVER_PORT", settings.MCP_SERVER_PORT))
    logger.info("Starting Harmonix360 MCP Server on port %d (Streamable HTTP)", port)
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
