"""Attendance (PS B3).

RBAC (Architecture §5): Employee has Create+Read on their OWN attendance; HR
Manager and above have full CRUD across everyone. Phase 2 splits those: this
collection route stays HR-only, and an Employee reaches their own records
through a self-scoped route that filters by the token's `employee_id` claim.
Listing every check-in in the company is not an employee-level read.
"""
from app.api.v1.routers._skeleton import build_list_router
from app.models.enums import HR_ROLES
from app.schemas.hr import AttendanceSummary
from app.services.attendance import AttendanceService

router = build_list_router(
    prefix="/attendance",
    tag="Attendance",
    allowed_roles=HR_ROLES,
    service_factory=AttendanceService,
    response_model=AttendanceSummary,
    summary="List attendance records",
)
