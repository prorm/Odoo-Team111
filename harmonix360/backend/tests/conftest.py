"""Shared fixtures for the smoke suite.

These tests run against a REAL Postgres (the same one alembic migrated) — the
Postgres EXCLUDE constraint that prevents double-booking cannot be exercised
against SQLite, and it is the single most important behaviour to keep green.
"""
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.entities import MeetingRoom, ResourceBooking, User
from app.repositories.meeting_room import MeetingRoomRepository

# Booking user. BookingService.create_booking defaults user_id=1 and
# resource_bookings.user_id is a FK to users.id, so a freshly migrated CI
# database needs this row to exist before any booking can be written.
BOOKING_USER_ID = 1


@pytest_asyncio.fixture(scope="session", autouse=True)
async def ensure_booking_user():
    async with AsyncSessionLocal() as session:
        if await session.get(User, BOOKING_USER_ID) is None:
            session.add(User(
                id=BOOKING_USER_ID,
                public_id="usr_smoketest",
                email="smoke@harmonix360.test",
                password_hash="!not-a-real-hash",
                name="Smoke Test User",
            ))
            await session.commit()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def session():
    async with AsyncSessionLocal() as s:
        yield s


async def _room_fixture(name: str, *, is_bookable: bool):
    """Yield a fresh MeetingRoom's public_id, then delete it and its bookings.

    Each test gets its own room, so bookings in one test can never collide
    with another test's (or a previous run's) rows via the EXCLUDE constraint.
    Teardown keeps repeated local runs from accumulating rows; these tests
    commit for real (the EXCLUDE constraint only fires on a real INSERT, so a
    rollback-per-test fixture would defeat the most important assertion).
    """
    async with AsyncSessionLocal() as s:
        room = await MeetingRoomRepository(s).create(
            MeetingRoom(public_id="temp", name=name, is_bookable=is_bookable)
        )
        await s.commit()
        room_id, public_id = room.id, room.public_id

    yield public_id

    async with AsyncSessionLocal() as s:
        await s.execute(
            delete(ResourceBooking).where(
                ResourceBooking.resource_type == "meeting_room",
                ResourceBooking.resource_id == room_id,
            )
        )
        await s.execute(delete(MeetingRoom).where(MeetingRoom.id == room_id))
        await s.commit()


@pytest_asyncio.fixture
async def room():
    async for public_id in _room_fixture("Smoke Test Room", is_bookable=True):
        yield public_id


@pytest_asyncio.fixture
async def unbookable_room():
    async for public_id in _room_fixture("Smoke Test Room (not bookable)", is_bookable=False):
        yield public_id
