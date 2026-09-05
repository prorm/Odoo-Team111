"""Payruns and payslips (PS B5-B8).

RBAC (Architecture §5): HR Payroll User has CRU, HR Payroll Manager has full
CRUD, and HR Manager has NO payroll access — the single most important denial
in the matrix, since it is what keeps "can manage people" separate from "can
see and change what they are paid".

Phase 5 adds compute/validate/mark-paid. Two obligations from Architecture §6
apply to those and not to the reads here: `Idempotency-Key` is required on
payrun create and compute, and compute runs under
`acquire_entity_lock(session, "payrun", id)`.
"""
from fastapi import APIRouter

from app.api.v1.routers._skeleton import build_list_router
from app.models.enums import PAYROLL_ROLES
from app.schemas.hr import PayrunSummary, PayslipSummary
from app.services.payroll import PayrunService, PayslipService

router = APIRouter()

router.include_router(
    build_list_router(
        prefix="/payruns",
        tag="Payroll",
        allowed_roles=PAYROLL_ROLES,
        service_factory=PayrunService,
        response_model=PayrunSummary,
        summary="List payruns",
    )
)
router.include_router(
    build_list_router(
        prefix="/payslips",
        tag="Payroll",
        allowed_roles=PAYROLL_ROLES,
        service_factory=PayslipService,
        response_model=PayslipSummary,
        summary="List payslips",
    )
)
