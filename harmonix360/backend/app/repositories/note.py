from app.models.entities import Note
from app.repositories.base import BaseRepository

class NoteRepository(BaseRepository[Note]):
    model = Note
    public_id_prefix = "note"
