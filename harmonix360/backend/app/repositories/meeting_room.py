from app.models.entities import MeetingRoom
from app.repositories.base import BaseRepository


class MeetingRoomRepository(BaseRepository[MeetingRoom]):
    """Minimal repository for the second resource type used to prove
    ResourceBooking's genericity. No service/router — see app/services/booking_resources.py."""
    model = MeetingRoom
    public_id_prefix = "room"
