"""Working schedules (PS A3).

RBAC (Architecture §5): HR Manager and above, full CRUD.

Note what the request schemas do NOT accept: `weekly_hours`. It is computed by
the service from the schedule's lines and appears only on the response, so
there is no request shape a client could use to set it. That is the strongest
form of "never manually entered" available — nothing to validate, because
nothing can be sent.
"""
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import CurrentUser, require_role
from app.core.database import get_db
from app.core.rate_limit import rate_limiter
from app.models.enums import HR_ROLES
from app.models.working_schedule import WorkingSchedule
from app.schemas.common import PaginatedResponse
from app.schemas.schedule import (
    WorkingScheduleCreate,
    WorkingScheduleResponse,
    WorkingScheduleUpdate,
)
from app.services.schedule import WorkingScheduleService

router = APIRouter(
    prefix="/working-schedules", tags=["Working Schedules"], dependencies=[Depends(rate_limiter)]
)


def _to_response(schedule: WorkingSchedule) -> WorkingScheduleResponse:
    return WorkingScheduleResponse.model_validate(schedule)


@router.get("/", response_model=PaginatedResponse[WorkingScheduleResponse], summary="List working schedules")
async def list_schedules(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(HR_ROLES)),
):
    service = WorkingScheduleService(db)
    rows, total = await service.list_with_lines(limit=limit, offset=offset)
    return PaginatedResponse(
        items=[_to_response(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.post("/", response_model=WorkingScheduleResponse, status_code=status.HTTP_201_CREATED)
async def create_schedule(
    dto: WorkingScheduleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(HR_ROLES)),
):
    service = WorkingScheduleService(db)
    schedule = await service.create_schedule(
        name=dto.name,
        schedule_type=dto.schedule_type,
        lines=dto.lines,
        actor_email=current_user.email,
    )
    return _to_response(schedule)


@router.get("/{public_id}", response_model=WorkingScheduleResponse)
async def get_schedule(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(HR_ROLES)),
):
    service = WorkingScheduleService(db)
    return _to_response(await service.get_or_404(public_id))


@router.patch("/{public_id}", response_model=WorkingScheduleResponse)
async def update_schedule(
    public_id: str,
    dto: WorkingScheduleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(HR_ROLES)),
):
    """Sending `lines` REPLACES the whole set and recomputes `weekly_hours`;
    omitting it leaves both alone."""
    service = WorkingScheduleService(db)
    schedule = await service.update_schedule(
        public_id,
        name=dto.name if "name" in dto.model_fields_set else None,
        schedule_type=dto.schedule_type if "schedule_type" in dto.model_fields_set else None,
        lines=dto.lines,
        actor_email=current_user.email,
    )
    return _to_response(schedule)


@router.delete("/{public_id}", response_model=WorkingScheduleResponse)
async def delete_schedule(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(HR_ROLES)),
):
    service = WorkingScheduleService(db)
    return _to_response(await service.delete_schedule(public_id, actor_email=current_user.email))
