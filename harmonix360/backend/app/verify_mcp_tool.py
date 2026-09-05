"""
Live proof that app/mcp/tool_wrapper.py's @mcp_tool(mcp) decorator is enough
to add a new MCP tool with no hand-written session/audit/error-handling
plumbing — calls create_note (app/mcp/server.py) through a real fastmcp.Client,
in-process against the `mcp` FastMCP server instance (no separate server process
needed for this proof; the same instance is what mcp.run() serves over
streamable-http in production).
"""
import asyncio
from fastmcp import Client
from app.core.config import settings
from app.mcp.server import mcp


async def main():
    async with Client(mcp) as client:
        print("=" * 80)
        print("1. list_tools — create_note is registered, `session` is NOT in its schema")
        print("=" * 80)
        tools = await client.list_tools()
        names = [t.name for t in tools]
        print("tools:", names)
        assert "create_note" in names
        create_note_tool = next(t for t in tools if t.name == "create_note")
        schema_props = list(create_note_tool.inputSchema.get("properties", {}).keys())
        print("create_note input schema properties:", schema_props)
        assert "session" not in schema_props
        assert "content" in schema_props and "api_key" in schema_props

        print("\n" + "=" * 80)
        print("2. call create_note WITHOUT api_key — must fail via the shared error envelope")
        print("=" * 80)
        result = await client.call_tool("create_note", {"content": "unauthorized attempt"})
        print("raw result:", result.data)
        assert result.data["status"] == "error"

        print("\n" + "=" * 80)
        print("3. call create_note WITH a valid api_key — entity-specific code only in server.py")
        print("=" * 80)
        result = await client.call_tool(
            "create_note",
            {"content": "Created via fastmcp.Client through @mcp_tool", "api_key": settings.MCP_AGENT_API_KEY},
        )
        print("raw result:", result.data)
        assert result.data["status"] == "success"
        assert result.data["note_id"].startswith("note_")

        print("\n" + "=" * 80)
        print("4. call an EXISTING @mcp_tool-wrapped tool (check_asset_status) to prove the "
              "decorator swap didn't change external behavior for tools that already existed")
        print("=" * 80)
        result = await client.call_tool("check_asset_status", {"asset_public_id": "not_a_real_id", "api_key": settings.MCP_AGENT_API_KEY})
        print("raw result (expected 404-shaped error, not a crash):", result.data)
        assert result.data["status"] == "error"

        print("\nDONE.")


if __name__ == "__main__":
    asyncio.run(main())
