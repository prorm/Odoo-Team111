"""Minimal pre-freeze smoke suite.

Deliberately small. It is not coverage — it is a tripwire for the handful of
framework behaviours a fresh problem statement leans on hardest, so that a
break in the foundation fails CI instead of failing silently on hackathon day:

  - the app boots and serves a request at all
  - BaseRepository's create -> public_id -> fetch round-trip works
    (every entity in Harmonix360 inherits this; if it breaks, everything breaks)
  - the generic booking path returns 201 for a registered resource type
  - the Postgres EXCLUDE constraint rejects an overlap as 409
    (Definition of Done, Section 12 — previously only ever proven by hand)
  - the resource registry rejects unknown types / missing / non-bookable
    resources with the right status codes rather than a 500
"""
from sqlalchemy import delete

from app.models.entities import MeetingRoom
from app.repositories.booking import BookingRepository
from app.repositories.meeting_room import MeetingRoomRepository

START = "2026-12-01T09:00:00Z"
END = "2026-12-01T10:00:00Z"
OVERLAP_START = "2026-12-01T09:30:00Z"
OVERLAP_END = "2026-12-01T10:30:00Z"


async def test_health_endpoint_returns_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_base_repository_round_trip(session):
    """create() assigns a prefixed public_id that get_by_public_id resolves back
    to the same row — the generic scaffolding every entity depends on."""
    repo = MeetingRoomRepository(session)
    created = await repo.create(MeetingRoom(public_id="temp", name="Round Trip", is_bookable=True))
    await session.commit()

    try:
        assert created.public_id.startswith("room_")
        fetched = await repo.get_by_public_id(created.public_id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.name == "Round Trip"
    finally:
        await session.execute(delete(MeetingRoom).where(MeetingRoom.id == created.id))
        await session.commit()


async def test_create_booking_returns_201(client, room):
    resp = await client.post("/api/v1/bookings/", json={
        "resource_type": "meeting_room",
        "resource_public_id": room,
        "start_time": START,
        "end_time": END,
        "purpose": "smoke",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["resource_type"] == "meeting_room"
    assert body["status"] == "CONFIRMED"
    assert body["id"].startswith("bkg_")


async def test_overlapping_booking_returns_409(client, room):
    """The Postgres EXCLUDE constraint — not application logic — must reject this."""
    first = await client.post("/api/v1/bookings/", json={
        "resource_type": "meeting_room",
        "resource_public_id": room,
        "start_time": START,
        "end_time": END,
    })
    assert first.status_code == 201, first.text

    second = await client.post("/api/v1/bookings/", json={
        "resource_type": "meeting_room",
        "resource_public_id": room,
        "start_time": OVERLAP_START,
        "end_time": OVERLAP_END,
    })
    assert second.status_code == 409, second.text
    assert "overlapping" in second.json()["detail"]


async def test_cancelled_booking_releases_its_slot(client, room):
    """A CANCELLED booking must stop reserving its time range.

    This is the payoff of the predicate added in migration
    010_booking_exclusion_predicate. Before it, the EXCLUDE constraint had no
    WHERE clause, so a cancelled booking went on blocking its slot forever and
    the second create below returned 409 with no live row to explain why.
    """
    created = await client.post("/api/v1/bookings/", json={
        "resource_type": "meeting_room",
        "resource_public_id": room,
        "start_time": START,
        "end_time": END,
    })
    assert created.status_code == 201, created.text
    booking_id = created.json()["id"]

    # Slot is taken while the booking is live.
    blocked = await client.post("/api/v1/bookings/", json={
        "resource_type": "meeting_room",
        "resource_public_id": room,
        "start_time": OVERLAP_START,
        "end_time": OVERLAP_END,
    })
    assert blocked.status_code == 409, blocked.text

    cancelled = await client.post(
        f"/api/v1/bookings/{booking_id}/override",
        json={"decision": "deny", "note": "freeing the slot"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"

    # Same overlapping request now succeeds.
    reused = await client.post("/api/v1/bookings/", json={
        "resource_type": "meeting_room",
        "resource_public_id": room,
        "start_time": OVERLAP_START,
        "end_time": OVERLAP_END,
    })
    assert reused.status_code == 201, reused.text
    assert reused.json()["status"] == "CONFIRMED"


async def test_soft_deleted_booking_releases_its_slot(client, room, session):
    """Soft-deleting a booking must release its slot too.

    The nastier half of the same bug: BaseRepository filters `deleted_at IS
    NULL` everywhere, so a soft-deleted booking is invisible to the whole API —
    yet without the predicate it still occupied the range, making the slot
    permanently unbookable with no row anyone could find or cancel.
    """
    created = await client.post("/api/v1/bookings/", json={
        "resource_type": "meeting_room",
        "resource_public_id": room,
        "start_time": START,
        "end_time": END,
    })
    assert created.status_code == 201, created.text
    booking_id = created.json()["id"]

    repo = BookingRepository(session)
    await repo.soft_delete(await repo.get_by_public_id(booking_id))
    await session.commit()

    reused = await client.post("/api/v1/bookings/", json={
        "resource_type": "meeting_room",
        "resource_public_id": room,
        "start_time": OVERLAP_START,
        "end_time": OVERLAP_END,
    })
    assert reused.status_code == 201, reused.text


async def test_unknown_resource_type_returns_400(client, room):
    resp = await client.post("/api/v1/bookings/", json={
        "resource_type": "spaceship",
        "resource_public_id": room,
        "start_time": START,
        "end_time": END,
    })
    assert resp.status_code == 400, resp.text
    assert "Unknown resource_type" in resp.json()["detail"]


async def test_missing_resource_returns_404(client):
    resp = await client.post("/api/v1/bookings/", json={
        "resource_type": "meeting_room",
        "resource_public_id": "room_doesnotexist",
        "start_time": START,
        "end_time": END,
    })
    assert resp.status_code == 404, resp.text


async def test_non_bookable_resource_returns_400(client, unbookable_room):
    resp = await client.post("/api/v1/bookings/", json={
        "resource_type": "meeting_room",
        "resource_public_id": unbookable_room,
        "start_time": START,
        "end_time": END,
    })
    assert resp.status_code == 400, resp.text
    assert "not bookable" in resp.json()["detail"]
