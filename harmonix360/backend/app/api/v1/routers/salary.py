"""Salary structures and rules (PS A5/A6).

RBAC (Architecture §5) — the one place in the matrix where read and write split
across roles:

    HR Payroll User     READ-ONLY on structures and rules
    HR Payroll Manager  full CRUD
    HR Manager          no access at all

So the read routes below use PAYROLL_ROLES, and every mutating route Phase 3
adds must use PAYROLL_ADMIN_ROLES. Putting one role set on the whole router
would hand authoring rights to the read-only role — and authoring a salary rule
is authoring what people get paid.
"""
from fastapi import APIRouter

from app.api.v1.routers._skeleton import build_list_router
from app.models.enums import PAYROLL_ROLES
from app.schemas.hr import SalaryRuleSummary, SalaryStructureSummary
from app.services.salary import SalaryRuleService, SalaryStructureService

router = APIRouter()

router.include_router(
    build_list_router(
        prefix="/salary-structures",
        tag="Salary Configuration",
        allowed_roles=PAYROLL_ROLES,
        service_factory=SalaryStructureService,
        response_model=SalaryStructureSummary,
        summary="List salary structures",
    )
)
router.include_router(
    build_list_router(
        prefix="/salary-rules",
        tag="Salary Configuration",
        allowed_roles=PAYROLL_ROLES,
        service_factory=SalaryRuleService,
        response_model=SalaryRuleSummary,
        summary="List salary rules",
    )
)
