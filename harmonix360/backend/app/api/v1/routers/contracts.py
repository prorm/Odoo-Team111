"""Contracts (PS A2).

RBAC (Architecture §5): HR Manager and above, full CRUD. Contracts carry wages,
so this is not a directory read — Employee is deliberately absent.
"""
from app.api.v1.routers._skeleton import build_list_router
from app.models.enums import HR_ROLES
from app.schemas.hr import ContractSummary
from app.services.contract import ContractService

router = build_list_router(
    prefix="/contracts",
    tag="Contracts",
    allowed_roles=HR_ROLES,
    service_factory=ContractService,
    response_model=ContractSummary,
    summary="List contracts",
)
