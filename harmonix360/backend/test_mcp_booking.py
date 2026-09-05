"""
Live MCP client proof for the generic create_booking tool.

Same shape as test_mcp.py (real fastmcp.Client over streamable-http against a
running app/mcp/server.py), exercising the post-refactor signature:
create_booking(resource_type, resource_public_id, start_time, end_time).

Proves through the actual MCP transport — not a direct service call — that:
  1. an Asset books via resource_type="asset"
  2. a MeetingRoom books via resource_type="meeting_room"
  3. an overlapping booking is rejected (the tool wrapper converts the
     BookingService HTTPException(409) into its {"status": "error"} envelope)
  4. an unregistered resource_type is rejected by the registry
"""
import asyncio
import sys

from fastmcp import Client

MCP_URL = "http://localhost:8100/mcp"
API_KEY = "harmonix360-mcp-dev-key"


async def call(client, label, args):
    print(f"\n--- {label} ---")
    print(f"    args: {args}")
    try:
        result = await client.call_tool("create_booking", {**args, "api_key": API_KEY})
        print(f"    -> {result.data}")
    except Exception as e:
        print(f"    !! exception: {type(e).__name__}: {e}")


async def main(asset_public_id, room_public_id):
    print("=== CONNECTING TO MCP SERVER ===")
    async with Client(MCP_URL) as client:
        print("\n--- 0. create_booking TOOL SCHEMA (post-refactor) ---")
        tools = await client.list_tools()
        for t in tools:
            if t.name == "create_booking":
                print(f"    inputSchema properties: {list(t.inputSchema['properties'].keys())}")
                print(f"    required: {t.inputSchema.get('required')}")

        await call(client, "1. BOOK ASSET (resource_type='asset')", {
            "resource_type": "asset",
            "resource_public_id": asset_public_id,
            "start_time": "2026-10-05T09:00:00Z",
            "end_time": "2026-10-05T10:00:00Z",
        })

        await call(client, "2. BOOK MEETING ROOM (resource_type='meeting_room')", {
            "resource_type": "meeting_room",
            "resource_public_id": room_public_id,
            "start_time": "2026-10-05T09:00:00Z",
            "end_time": "2026-10-05T10:00:00Z",
        })

        await call(client, "3. OVERLAPPING ASSET BOOKING (expect 409 error envelope)", {
            "resource_type": "asset",
            "resource_public_id": asset_public_id,
            "start_time": "2026-10-05T09:30:00Z",
            "end_time": "2026-10-05T10:30:00Z",
        })

        await call(client, "4. OVERLAPPING MEETING ROOM BOOKING (expect 409 error envelope)", {
            "resource_type": "meeting_room",
            "resource_public_id": room_public_id,
            "start_time": "2026-10-05T09:15:00Z",
            "end_time": "2026-10-05T09:45:00Z",
        })

        await call(client, "5. UNREGISTERED resource_type (expect registry rejection)", {
            "resource_type": "spaceship",
            "resource_public_id": asset_public_id,
            "start_time": "2026-10-05T14:00:00Z",
            "end_time": "2026-10-05T15:00:00Z",
        })


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
