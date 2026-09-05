"""Employees (PS A1/B2).

RBAC (Architecture §5): HR Manager and above have full CRUD. An Employee has
no module-level access at all — their rights are row-scoped to their OWN
record, which Phase 1 adds as a separate self-service route rather than by
loosening this one. Widening `HR_ROLES` here would expose the whole staff
directory, salaries included, to every logged-in employee.
"""
from app.api.v1.routers._skeleton import build_list_router
from app.models.enums import HR_ROLES
from app.schemas.hr import EmployeeSummary
from app.services.employee import EmployeeService

router = build_list_router(
    prefix="/employees",
    tag="Employees",
    allowed_roles=HR_ROLES,
    service_factory=EmployeeService,
    response_model=EmployeeSummary,
    summary="List employees",
)
