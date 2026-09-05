from app.models.entities import ResourceBooking
from app.repositories.base import BaseRepository

class BookingRepository(BaseRepository[ResourceBooking]):
    model = ResourceBooking
    public_id_prefix = "bkg"
