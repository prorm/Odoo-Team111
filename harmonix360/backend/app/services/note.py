from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.note import NoteRepository
from app.models.entities import Note
from app.services.base import BaseService


class NoteService(BaseService[Note]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, NoteRepository(session), entity_name="Note")

    async def create_note(self, content: str, actor_email: str) -> Note:
        note = Note(public_id="temp", content=content)
        return await self.create(note, actor=actor_email, action="CREATE_NOTE", after_diff={"content": content})

    async def update_note(self, public_id: str, content: str, actor_email: str) -> Note:
        note = await self.get_or_404(public_id)
        before = {"content": note.content}
        note.content = content
        return await self.update(note, actor=actor_email, action="UPDATE_NOTE", before_diff=before, after_diff={"content": content})

    async def delete_note(self, public_id: str, actor_email: str) -> Note:
        note = await self.get_or_404(public_id)
        return await self.soft_delete(note, actor=actor_email, action="DELETE_NOTE")
