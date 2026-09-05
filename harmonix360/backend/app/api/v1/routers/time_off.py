"""Time off — types, allocations and requests (PS A4/B4).

RBAC (Architecture §5): HR Manager and above have full CRUD plus
approve/refuse. An Employee may create and read their OWN requests and read
their own balances — Phase 2 adds those as self-scoped routes filtered by the
token's `employee_id` claim, not by widening these collection reads. Who is on
leave and why is not an all-staff read.
"""
from fastapi import APIRouter

from app.api.v1.routers._skeleton import build_list_router
from app.models.enums import HR_ROLES
from app.schemas.hr import TimeOffAllocationSummary, TimeOffRequestSummary, TimeOffTypeSummary
from app.services.time_off import TimeOffAllocationService, TimeOffRequestService, TimeOffTypeService

router = APIRouter()

router.include_router(
    build_list_router(
        prefix="/time-off-types",
        tag="Time Off",
        allowed_roles=HR_ROLES,
        service_factory=TimeOffTypeService,
        response_model=TimeOffTypeSummary,
        summary="List time off types",
    )
)
router.include_router(
    build_list_router(
        prefix="/time-off-allocations",
        tag="Time Off",
        allowed_roles=HR_ROLES,
        service_factory=TimeOffAllocationService,
        response_model=TimeOffAllocationSummary,
        summary="List time off allocations",
    )
)
router.include_router(
    build_list_router(
        prefix="/time-off-requests",
        tag="Time Off",
        allowed_roles=HR_ROLES,
        service_factory=TimeOffRequestService,
        response_model=TimeOffRequestSummary,
        summary="List time off requests",
    )
)
