from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.rate_limit import rate_limiter
from app.api.v1.deps import get_current_user, CurrentUser
from app.schemas.note import NoteCreate, NoteUpdate, NoteResponse
from app.schemas.common import PaginatedResponse
from app.services.note import NoteService

router = APIRouter(prefix="/notes", tags=["Notes"], dependencies=[Depends(rate_limiter)])


def _to_response(note) -> NoteResponse:
    return NoteResponse(
        id=note.public_id,
        content=note.content,
        version=note.version,
        tenant_id=note.tenant_id,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


@router.post("/", response_model=NoteResponse, status_code=status.HTTP_201_CREATED)
async def create_note(
    dto: NoteCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    service = NoteService(db)
    note = await service.create_note(content=dto.content, actor_email=current_user.email)
    return _to_response(note)


@router.get("/", response_model=PaginatedResponse[NoteResponse])
async def list_notes(
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    service = NoteService(db)
    notes, total = await service.list(limit=limit, offset=offset)
    return PaginatedResponse(items=[_to_response(n) for n in notes], total=total, limit=limit, offset=offset)


@router.get("/{public_id}", response_model=NoteResponse)
async def get_note(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    service = NoteService(db)
    note = await service.get_or_404(public_id)
    return _to_response(note)


@router.patch("/{public_id}", response_model=NoteResponse)
async def update_note(
    public_id: str,
    dto: NoteUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    service = NoteService(db)
    note = await service.update_note(public_id, content=dto.content, actor_email=current_user.email)
    return _to_response(note)


@router.delete("/{public_id}", response_model=NoteResponse)
async def delete_note(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    service = NoteService(db)
    note = await service.delete_note(public_id, actor_email=current_user.email)
    return _to_response(note)
