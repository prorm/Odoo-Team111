"""
Generic ResourceBooking proof — demonstrates the booking framework is no
longer Asset-specific:
  1. Book an Asset (existing resource type) -> succeeds.
  2. Book a MeetingRoom (new resource type, added purely via
     app/services/booking_resources.py's registry entry) -> succeeds.
  3. Attempt an overlapping booking on the same Asset -> Postgres EXCLUDE
     constraint fires -> BookingService translates it -> HTTP 409.
  4. Attempt an overlapping booking on the same MeetingRoom -> same 409 path,
     proving the constraint and the error translation are resource-type-
     agnostic, not Asset-specific logic wearing a thin disguise.

No auth token is passed — app.api.v1.deps.get_current_user's non-production
dev fallback resolves anonymous requests to an ADMIN user, same convention
test_mcp.py and verify.py already rely on.

MeetingRoom has deliberately no CRUD API (task scope: prove genericity, not
build a second ERP module) — this script creates its one demo row directly
through MeetingRoomRepository, the same repository the app itself uses.
"""
import asyncio
import sys
from pprint import pprint

import requests

from app.core.database import AsyncSessionLocal
from app.models.entities import MeetingRoom
from app.repositories.meeting_room import MeetingRoomRepository

BASE_URL = "http://localhost:8000/api/v1"


def print_step(step_num, title):
    print(f"\n{'=' * 80}\nSTEP {step_num}: {title}\n{'=' * 80}")


async def _get_or_create_demo_room() -> str:
    async with AsyncSessionLocal() as session:
        repo = MeetingRoomRepository(session)
        room = MeetingRoom(public_id="temp", name="Generic Booking Proof Room", is_bookable=True)
        room = await repo.create(room)
        await session.commit()
        return room.public_id


def main():
    print("Starting Generic ResourceBooking Verification Trace\n")

    print_step(1, "Find a bookable Asset")
    resp = requests.get(f"{BASE_URL}/assets/?limit=100")
    resp.raise_for_status()
    assets = resp.json()["items"]
    bookable_assets = [a for a in assets if a["is_bookable"]]
    if not bookable_assets:
        print("No bookable assets found — aborting.")
        sys.exit(1)
    asset = bookable_assets[0]
    print(f"Using asset: {asset['id']} ({asset['name']})")

    print_step(2, "Create the demo MeetingRoom (second resource type, no CRUD API by design) "
                  "via MeetingRoomRepository directly")
    room_public_id = asyncio.run(_get_or_create_demo_room())
    print(f"Created meeting_room: {room_public_id}")

    start = "2026-09-01T09:00:00Z"
    end = "2026-09-01T10:00:00Z"

    print_step(3, "Book the Asset (resource_type='asset') -> expect HTTP 201")
    resp = requests.post(f"{BASE_URL}/bookings/", json={
        "resource_type": "asset",
        "resource_public_id": asset["id"],
        "start_time": start,
        "end_time": end,
        "purpose": "Generic booking proof — asset",
    })
    print(f"HTTP {resp.status_code}")
    pprint(resp.json())
    assert resp.status_code == 201, "Expected 201 booking the asset"

    print_step(4, "Book the MeetingRoom (resource_type='meeting_room') -> expect HTTP 201")
    resp = requests.post(f"{BASE_URL}/bookings/", json={
        "resource_type": "meeting_room",
        "resource_public_id": room_public_id,
        "start_time": start,
        "end_time": end,
        "purpose": "Generic booking proof — meeting room",
    })
    print(f"HTTP {resp.status_code}")
    pprint(resp.json())
    assert resp.status_code == 201, "Expected 201 booking the meeting room"

    print_step(5, "Book the SAME Asset for an OVERLAPPING window -> expect HTTP 409")
    resp = requests.post(f"{BASE_URL}/bookings/", json={
        "resource_type": "asset",
        "resource_public_id": asset["id"],
        "start_time": "2026-09-01T09:30:00Z",
        "end_time": "2026-09-01T10:30:00Z",
        "purpose": "Should be rejected — overlaps step 3",
    })
    print(f"HTTP {resp.status_code}")
    pprint(resp.json())
    assert resp.status_code == 409, f"Expected 409, got {resp.status_code}"

    print_step(6, "Book the SAME MeetingRoom for an OVERLAPPING window -> expect HTTP 409")
    resp = requests.post(f"{BASE_URL}/bookings/", json={
        "resource_type": "meeting_room",
        "resource_public_id": room_public_id,
        "start_time": "2026-09-01T09:15:00Z",
        "end_time": "2026-09-01T09:45:00Z",
        "purpose": "Should be rejected — overlaps step 4",
    })
    print(f"HTTP {resp.status_code}")
    pprint(resp.json())
    assert resp.status_code == 409, f"Expected 409, got {resp.status_code}"

    print_step(7, "Non-overlapping booking on the Asset AFTER the first window -> expect HTTP 201")
    resp = requests.post(f"{BASE_URL}/bookings/", json={
        "resource_type": "asset",
        "resource_public_id": asset["id"],
        "start_time": "2026-09-01T11:00:00Z",
        "end_time": "2026-09-01T12:00:00Z",
        "purpose": "Non-overlapping — should succeed",
    })
    print(f"HTTP {resp.status_code}")
    pprint(resp.json())
    assert resp.status_code == 201, "Expected 201 for a non-overlapping window"

    print("\nAll assertions passed: generic booking works for two independent "
          "resource types through the same code path, and the Postgres EXCLUDE "
          "constraint correctly rejects overlaps for both as HTTP 409.")


if __name__ == "__main__":
    main()
