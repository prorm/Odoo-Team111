import sys
import asyncio
from fastmcp import Client

async def main(asset_id):
    print("=== CONNECTING TO MCP SERVER ===")
    async with Client("http://mcp-server:8100/mcp") as client:
        print("\n--- 1. LIST TOOLS ---")
        tools = await client.list_tools()
        print(tools)

        print("\n--- 2. CALL check_asset_status ---")
        try:
            result = await client.call_tool("check_asset_status", {"asset_public_id": asset_id, "api_key": "harmonix360-mcp-dev-key"})
            print(result)
        except Exception as e:
            print("check_asset_status error:", e)

        print("\n--- 3. CALL query_audit_trail ---")
        try:
            result_audit = await client.call_tool("query_audit_trail", {"entity_public_id": asset_id, "api_key": "harmonix360-mcp-dev-key"})
            print(result_audit)
        except Exception as e:
            print("query_audit_trail error:", e)

if __name__ == "__main__":
    asset_id = sys.argv[1] if len(sys.argv) > 1 else "R360QwJ2"
    asyncio.run(main(asset_id))
