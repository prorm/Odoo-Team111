"""Salary structures and rules (PS A5/A6).

RBAC (Architecture §5) — the one place in the matrix where read and write
split across roles:

    HR Payroll User     READ-ONLY on structures and rules
    HR Payroll Manager  full CRUD
    HR Manager          no access at all

So every GET below uses PAYROLL_ROLES and every mutating route uses
PAYROLL_ADMIN_ROLES. Putting one role set on the whole router would hand
authoring rights to the read-only role — and authoring a salary rule is
authoring what people get paid.
"""
from typing import List

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import CurrentUser, require_role
from app.core.database import get_db
from app.core.rate_limit import rate_limiter
from app.models.enums import PAYROLL_ADMIN_ROLES, PAYROLL_ROLES
from app.models.salary import SalaryRule, SalaryStructure
from app.schemas.common import PaginatedResponse
from app.schemas.salary import (
    SalaryRuleCreate,
    SalaryRuleResponse,
    SalaryRuleUpdate,
    SalaryStructureCreate,
    SalaryStructureResponse,
    SalaryStructureUpdate,
)
from app.services.salary import SalaryRuleService, SalaryStructureService

router = APIRouter(dependencies=[Depends(rate_limiter)])

rules_router = APIRouter(prefix="/salary-rules", tags=["Salary Configuration"])
structures_router = APIRouter(prefix="/salary-structures", tags=["Salary Configuration"])


def _rule_response(rule: SalaryRule) -> SalaryRuleResponse:
    return SalaryRuleResponse.model_validate(rule)


def _structure_response(structure: SalaryStructure, usage_counts: dict[int, int]) -> SalaryStructureResponse:
    response = SalaryStructureResponse.model_validate(structure)
    # SERVER-COMPUTED (PS A5: "List/Form show rule count and Contract-usage
    # count"). Set here, against the whole page's usage counts, rather than
    # per row — same two-step pattern as ContractResponse.is_currently_active.
    response.rule_count = len(structure.rule_links)
    response.contract_usage_count = usage_counts.get(structure.id, 0)
    return response


async def _respond_structures(
    service: SalaryStructureService, structures: List[SalaryStructure]
) -> List[SalaryStructureResponse]:
    usage_counts = await service.contract_usage_counts(structures)
    return [_structure_response(s, usage_counts) for s in structures]


# --------------------------------------------------------------------- rules

@rules_router.get("/", response_model=PaginatedResponse[SalaryRuleResponse], summary="List salary rules")
async def list_rules(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    service = SalaryRuleService(db)
    rows, total = await service.list(limit=limit, offset=offset)
    return PaginatedResponse(items=[_rule_response(r) for r in rows], total=total, limit=limit, offset=offset)


@rules_router.post("/", response_model=SalaryRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_rule(
    dto: SalaryRuleCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ADMIN_ROLES)),
):
    service = SalaryRuleService(db)
    rule = await service.create_rule(dto, actor_email=current_user.email)
    return _rule_response(rule)


@rules_router.get("/{public_id}", response_model=SalaryRuleResponse)
async def get_rule(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    service = SalaryRuleService(db)
    return _rule_response(await service.get_or_404(public_id))


@rules_router.patch("/{public_id}", response_model=SalaryRuleResponse)
async def update_rule(
    public_id: str,
    dto: SalaryRuleUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ADMIN_ROLES)),
):
    service = SalaryRuleService(db)
    rule = await service.update_rule(public_id, dto, actor_email=current_user.email)
    return _rule_response(rule)


@rules_router.delete("/{public_id}", response_model=SalaryRuleResponse)
async def delete_rule(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ADMIN_ROLES)),
):
    service = SalaryRuleService(db)
    return _rule_response(await service.delete_rule(public_id, actor_email=current_user.email))


# ---------------------------------------------------------------- structures

@structures_router.get(
    "/", response_model=PaginatedResponse[SalaryStructureResponse], summary="List salary structures"
)
async def list_structures(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    service = SalaryStructureService(db)
    rows, total = await service.list_with_rules(limit=limit, offset=offset)
    return PaginatedResponse(
        items=await _respond_structures(service, rows), total=total, limit=limit, offset=offset
    )


@structures_router.post(
    "/",
    response_model=SalaryStructureResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {
            "description": (
                "A linked rule's percentage_base_code or formula references a rule that has not "
                "run yet in this structure's sequence, or an unknown name — refused at save time, "
                "per Architecture §7."
            )
        }
    },
)
async def create_structure(
    dto: SalaryStructureCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ADMIN_ROLES)),
):
    service = SalaryStructureService(db)
    structure = await service.create_structure(dto, actor_email=current_user.email)
    return (await _respond_structures(service, [structure]))[0]


@structures_router.get("/{public_id}", response_model=SalaryStructureResponse)
async def get_structure(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    service = SalaryStructureService(db)
    structure = await service.get_or_404(public_id)
    return (await _respond_structures(service, [structure]))[0]


@structures_router.patch(
    "/{public_id}",
    response_model=SalaryStructureResponse,
    responses={
        400: {
            "description": (
                "A linked rule's percentage_base_code or formula references a rule that has not "
                "run yet in the new sequence, or an unknown name."
            )
        }
    },
)
async def update_structure(
    public_id: str,
    dto: SalaryStructureUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ADMIN_ROLES)),
):
    """Sending `rules` REPLACES the whole ordered set; omitting it leaves the
    existing rules alone — same convention as `WorkingScheduleUpdate.lines`."""
    service = SalaryStructureService(db)
    structure = await service.update_structure(public_id, dto, actor_email=current_user.email)
    return (await _respond_structures(service, [structure]))[0]


@structures_router.delete("/{public_id}", response_model=SalaryStructureResponse)
async def delete_structure(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ADMIN_ROLES)),
):
    service = SalaryStructureService(db)
    structure = await service.delete_structure(public_id, actor_email=current_user.email)
    return (await _respond_structures(service, [structure]))[0]


router.include_router(rules_router)
router.include_router(structures_router)
