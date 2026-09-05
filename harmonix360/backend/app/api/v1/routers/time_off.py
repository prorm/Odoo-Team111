"""Time off HTTP adapters. Mutations authorize inside the services."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import CurrentUser, get_current_user, require_role
from app.core.database import get_db
from app.core.rate_limit import rate_limiter
from app.core.security import encode_public_id
from app.models.enums import HR_ROLES
from app.schemas.common import PaginatedResponse
from app.schemas.time_off import (
    AllocationCreate,
    AllocationResponse,
    AllocationUpdate,
    Decision,
    RequestCreate,
    RequestResponse,
    RequestUpdate,
    TypeCreate,
    TypeResponse,
    TypeUpdate,
)
from app.services.hr_access import scoped_list
from app.services.time_off import (
    TimeOffAllocationService,
    TimeOffRequestService,
    TimeOffTypeService,
)

router = APIRouter(tags=["Time Off"], dependencies=[Depends(rate_limiter)])


def request_response(row):
    data = {
        key: getattr(row, key)
        for key in (
            "public_id",
            "date_from",
            "date_to",
            "duration",
            "status",
            "version",
            "employee",
            "time_off_type",
            "reason",
            "decision_note",
        )
    }
    data["approved_by"] = (
        encode_public_id(row.approved_by, "usr") if row.approved_by else None
    )
    data["allocation_id"] = (
        encode_public_id(row.allocation_id, "alloc") if row.allocation_id else None
    )
    return RequestResponse.model_validate(data)


@router.get("/time-off-types/", response_model=PaginatedResponse[TypeResponse])
async def list_types(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_role(HR_ROLES)),
):
    rows, total = await TimeOffTypeService(db).list(limit=limit, offset=offset)
    return PaginatedResponse(
        items=[TypeResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/time-off-types/lookup", response_model=PaginatedResponse[TypeResponse])
async def lookup_types(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Policy choices needed to submit leave; contains no employee records."""
    rows, total = await TimeOffTypeService(db).list(limit=limit, offset=offset)
    return PaginatedResponse(
        items=[TypeResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/time-off-types/{public_id}", response_model=TypeResponse)
async def read_type(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(require_role(HR_ROLES)),
):
    return await TimeOffTypeService(db).get_or_404(public_id)


@router.post("/time-off-types/", response_model=TypeResponse, status_code=201)
async def create_type(
    dto: TypeCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await TimeOffTypeService(db).save_type(dto, user)


@router.patch("/time-off-types/{public_id}", response_model=TypeResponse)
async def update_type(
    public_id: str,
    dto: TypeUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await TimeOffTypeService(db).save_type(dto, user, public_id)


@router.delete("/time-off-types/{public_id}", response_model=TypeResponse)
async def delete_type(
    public_id: str,
    version: int = Query(ge=1),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return await TimeOffTypeService(db).delete_type(public_id, version, user)


@router.get(
    "/time-off-allocations/", response_model=PaginatedResponse[AllocationResponse]
)
async def list_allocations(
    employee_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    rows, total = await scoped_list(
        TimeOffAllocationService(db),
        user,
        employee_id,
        own=False,
        limit=limit,
        offset=offset,
    )
    return PaginatedResponse(
        items=[AllocationResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/time-off-allocations/me", response_model=PaginatedResponse[AllocationResponse]
)
async def own_allocations(
    employee_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    rows, total = await scoped_list(
        TimeOffAllocationService(db),
        user,
        employee_id,
        own=True,
        limit=limit,
        offset=offset,
    )
    return PaginatedResponse(
        items=[AllocationResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/time-off-allocations/{public_id}", response_model=AllocationResponse)
async def read_allocation(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return AllocationResponse.model_validate(
        await TimeOffAllocationService(db).read(public_id, user)
    )


@router.post(
    "/time-off-allocations/", response_model=AllocationResponse, status_code=201
)
async def create_allocation(
    dto: AllocationCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return AllocationResponse.model_validate(
        await TimeOffAllocationService(db).create_allocation(dto, user)
    )


@router.patch("/time-off-allocations/{public_id}", response_model=AllocationResponse)
async def update_allocation(
    public_id: str,
    dto: AllocationUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return AllocationResponse.model_validate(
        await TimeOffAllocationService(db).update_allocation(public_id, dto, user)
    )


@router.delete("/time-off-allocations/{public_id}", response_model=AllocationResponse)
async def delete_allocation(
    public_id: str,
    version: int = Query(ge=1),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return AllocationResponse.model_validate(
        await TimeOffAllocationService(db).delete_allocation(public_id, version, user)
    )


@router.get("/time-off-requests/", response_model=PaginatedResponse[RequestResponse])
async def list_requests(
    employee_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    rows, total = await scoped_list(
        TimeOffRequestService(db),
        user,
        employee_id,
        own=False,
        limit=limit,
        offset=offset,
    )
    return PaginatedResponse(
        items=[request_response(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/time-off-requests/me", response_model=PaginatedResponse[RequestResponse])
async def own_requests(
    employee_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    rows, total = await scoped_list(
        TimeOffRequestService(db),
        user,
        employee_id,
        own=True,
        limit=limit,
        offset=offset,
    )
    return PaginatedResponse(
        items=[request_response(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/time-off-requests/{public_id}", response_model=RequestResponse)
async def read_request(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return request_response(await TimeOffRequestService(db).read(public_id, user))


@router.post("/time-off-requests/", response_model=RequestResponse, status_code=201)
async def create_request(
    dto: RequestCreate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return request_response(await TimeOffRequestService(db).create_request(dto, user))


@router.patch("/time-off-requests/{public_id}", response_model=RequestResponse)
async def update_request(
    public_id: str,
    dto: RequestUpdate,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return request_response(
        await TimeOffRequestService(db).update_request(public_id, dto, user)
    )


@router.delete("/time-off-requests/{public_id}", response_model=RequestResponse)
async def delete_request(
    public_id: str,
    version: int = Query(ge=1),
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return request_response(
        await TimeOffRequestService(db).delete_request(public_id, version, user)
    )


@router.post("/time-off-requests/{public_id}/approve", response_model=RequestResponse)
async def approve(
    public_id: str,
    dto: Decision,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return request_response(
        await TimeOffRequestService(db).decide(public_id, dto, user, approve=True)
    )


@router.post("/time-off-requests/{public_id}/refuse", response_model=RequestResponse)
async def refuse(
    public_id: str,
    dto: Decision,
    db: AsyncSession = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return request_response(
        await TimeOffRequestService(db).decide(public_id, dto, user, approve=False)
    )
