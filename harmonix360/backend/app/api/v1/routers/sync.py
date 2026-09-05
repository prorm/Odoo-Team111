from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.rate_limit import rate_limiter
from app.api.v1.deps import get_current_user, CurrentUser
from app.schemas.sync import PullResponse, PushRequest, PushResponse
from app.services.sync import SyncService
from app.services.sync_registry import registered_entity_types

router = APIRouter(prefix="/sync", tags=["Sync"], dependencies=[Depends(rate_limiter)])


@router.get("/pull", response_model=PullResponse)
async def pull(
    entity_types: str = Query(..., description="Comma-separated syncable entity types, e.g. 'attendance,time_off_request'"),
    since: Optional[str] = Query(None, description="Opaque cursor from a previous pull response"),
    limit: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    requested: List[str] = [t.strip() for t in entity_types.split(",") if t.strip()]
    unknown = [t for t in requested if t not in registered_entity_types()]
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown entity_types {unknown}. Registered: {registered_entity_types()}",
        )
    service = SyncService(db)
    # The principal goes with the request: an entity that scopes its rows per
    # employee (both of the ones registered here do) cannot be served without
    # knowing whose rows to serve.
    return await service.pull(
        since=since, entity_types=requested, limit=limit, user=current_user
    )


@router.post("/push", response_model=PushResponse)
async def push(
    dto: PushRequest,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    service = SyncService(db)
    return await service.push(mutations=dto.mutations, user=current_user)
