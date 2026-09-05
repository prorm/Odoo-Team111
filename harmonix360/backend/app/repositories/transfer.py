from app.models.entities import TransferRequest
from app.repositories.base import BaseRepository

class TransferRepository(BaseRepository[TransferRequest]):
    model = TransferRequest
    public_id_prefix = "trf"
