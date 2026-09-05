"""Working schedules (PS A3).

RBAC (Architecture §5): HR Manager and above, full CRUD.
"""
from app.api.v1.routers._skeleton import build_list_router
from app.models.enums import HR_ROLES
from app.schemas.hr import WorkingScheduleSummary
from app.services.schedule import WorkingScheduleService

router = build_list_router(
    prefix="/working-schedules",
    tag="Working Schedules",
    allowed_roles=HR_ROLES,
    service_factory=WorkingScheduleService,
    response_model=WorkingScheduleSummary,
    summary="List working schedules",
)
