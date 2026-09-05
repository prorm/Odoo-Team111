"""Registers the resource types Harmonix360 currently knows how to book.

This is the ONLY file that knows both "ResourceBooking" and "Asset"/"MeetingRoom"
exist at the same time — app/services/booking.py itself never imports
AssetRepository or MeetingRoomRepository. Adding a third bookable resource type
means adding one resolver + one register_resource_type call here; nothing in
the booking model/repository/service changes.
"""
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.resource_registry import register_resource_type
from app.repositories.asset import AssetRepository
from app.repositories.meeting_room import MeetingRoomRepository


async def _resolve_asset(session: AsyncSession, public_id: str) -> Optional[Tuple[int, bool]]:
    asset = await AssetRepository(session).get_by_public_id(public_id)
    if not asset:
        return None
    return asset.id, asset.is_bookable


async def _resolve_meeting_room(session: AsyncSession, public_id: str) -> Optional[Tuple[int, bool]]:
    room = await MeetingRoomRepository(session).get_by_public_id(public_id)
    if not room:
        return None
    return room.id, room.is_bookable


register_resource_type("asset", _resolve_asset)
register_resource_type("meeting_room", _resolve_meeting_room)
