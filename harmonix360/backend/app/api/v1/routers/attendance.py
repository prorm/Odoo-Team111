"""Attendance routes; service methods enforce permissions on every write."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import CurrentUser, get_current_user
from app.core.database import get_db
from app.core.rate_limit import rate_limiter
from app.core.security import encode_public_id
from app.schemas.attendance import (
    AttendanceCheckout,
    AttendanceCorrection,
    AttendanceCreate,
    AttendanceResponse,
)
from app.schemas.common import PaginatedResponse
from app.services.attendance import AttendanceService
from app.services.hr_access import scoped_list

router = APIRouter(
    prefix="/attendance", tags=["Attendance"], dependencies=[Depends(rate_limiter)]
)


class CheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: str


def response(row, service):
    # Don't mutate the tracked status on a read: it is derived for display.
    data = {
        key: getattr(row, key)
        for key in (
            "public_id",
            "check_in",
            "check_out",
            "worked_hours",
            "version",
            "employee",
            "correction_reason",
        )
    }
    data["status"] = service.status_for(row)
    data["corrected_by"] = (
        encode_public_id(row.corrected_by, "usr") if row.corrected_by else None
    )
    return AttendanceResponse.model_validate(data)


async def listing(db, user, employee_id, own, limit, offset):
    service = AttendanceService(db)
    rows, total = await scoped_list(
        service, user, employee_id, own=own, limit=limit, offset=offset
    )
    return PaginatedResponse(
        items=[response(row, service) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/", response_model=PaginatedResponse[AttendanceResponse])
async def list_attendance(
    employee_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await listing(db, user, employee_id, False, limit, offset)


@router.get("/me", response_model=PaginatedResponse[AttendanceResponse])
async def own_attendance(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await listing(db, user, None, True, limit, offset)


@router.post("/", response_model=AttendanceResponse, status_code=201)
async def create(
    dto: AttendanceCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = AttendanceService(db)
    return response(await service.create_attendance(dto, user), service)


@router.post("/check-in", response_model=AttendanceResponse, status_code=201)
async def check_in(
    dto: CheckIn,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = AttendanceService(db)
    return response(await service.check_in(dto.employee_id, user), service)


@router.get("/{public_id}", response_model=AttendanceResponse)
async def read(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = AttendanceService(db)
    return response(await service.read(public_id, user), service)


@router.post("/{public_id}/check-out", response_model=AttendanceResponse)
async def check_out(
    public_id: str,
    dto: AttendanceCheckout,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = AttendanceService(db)
    return response(await service.check_out(public_id, dto, user), service)


@router.patch("/{public_id}", response_model=AttendanceResponse)
async def correct(
    public_id: str,
    dto: AttendanceCorrection,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = AttendanceService(db)
    return response(await service.correct(public_id, dto, user), service)


@router.delete("/{public_id}", response_model=AttendanceResponse)
async def delete(
    public_id: str,
    version: int = Query(ge=1),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = AttendanceService(db)
    return response(await service.delete_attendance(public_id, version, user), service)
