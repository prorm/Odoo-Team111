"""Contracts (PS A2).

RBAC (Architecture §5): HR Manager and above, full CRUD. Employee is absent —
contracts carry wages, so this is not a directory read.

The 409 these endpoints can return is the important one. Creating or activating
a contract that overlaps an employee's existing active contract is refused by
the database (`contracts_active_period_overlap_excl`), and the service
translates SQLSTATE 23P01 into a 409 whose message names the conflicting
contract and its period. It is a normal, expected outcome — HR renewing a
contract without ending the previous one will hit it routinely — so the
frontend renders it inline on the form, never as a crash.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import CurrentUser, require_role
from app.core.database import get_db
from app.core.rate_limit import rate_limiter
from app.models.contract import Contract
from app.models.enums import HR_ROLES, ContractStatus
from app.schemas.common import PaginatedResponse
from app.schemas.contract import ContractCreate, ContractResponse, ContractUpdate
from app.services.contract import ContractService

router = APIRouter(prefix="/contracts", tags=["Contracts"], dependencies=[Depends(rate_limiter)])


def _to_response(contract: Contract, active_ids: set[int]) -> ContractResponse:
    response = ContractResponse.model_validate(contract)
    # Server-computed (PS A2). Set here rather than on the model so the "is
    # this the active one?" question is answered once per page, against the
    # employee's whole contract set, instead of per row.
    response.is_currently_active = contract.id in active_ids
    return response


async def _respond(service: ContractService, contracts: List[Contract]) -> List[ContractResponse]:
    active_ids = await service.active_contract_ids(contracts)
    return [_to_response(contract, active_ids) for contract in contracts]


@router.get("/", response_model=PaginatedResponse[ContractResponse], summary="List contracts")
async def list_contracts(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    employee_id: Optional[str] = Query(None, description="Filter to one employee's contract history"),
    status_filter: Optional[ContractStatus] = Query(None, alias="status"),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(HR_ROLES)),
):
    """`is_currently_active` on each row is computed server-side against the
    employee's full contract set — see ContractService.active_contract_ids for
    why the client cannot correctly derive it from a page."""
    service = ContractService(db)
    rows, total = await service.list_contracts(
        limit=limit, offset=offset, employee_id=employee_id, contract_status=status_filter
    )
    return PaginatedResponse(
        items=await _respond(service, rows), total=total, limit=limit, offset=offset
    )


@router.post(
    "/",
    response_model=ContractResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {
            "description": (
                "This employee already has an active contract overlapping the requested period. "
                "Refused by a Postgres EXCLUDE constraint, not by application logic — payroll "
                "must resolve exactly one contract per period."
            )
        }
    },
)
async def create_contract(
    dto: ContractCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(HR_ROLES)),
):
    service = ContractService(db)
    contract = await service.create_contract(dto, actor_email=current_user.email)
    return (await _respond(service, [contract]))[0]


@router.get("/{public_id}", response_model=ContractResponse)
async def get_contract(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(HR_ROLES)),
):
    service = ContractService(db)
    contract = await service.get_or_404(public_id)
    return (await _respond(service, [contract]))[0]


@router.patch(
    "/{public_id}",
    response_model=ContractResponse,
    responses={
        409: {
            "description": (
                "The requested dates, or activating this contract, would overlap another active "
                "contract for the same employee. A status-only change to 'active' can trigger "
                "this — it moves the row into the constrained set."
            )
        }
    },
)
async def update_contract(
    public_id: str,
    dto: ContractUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(HR_ROLES)),
):
    service = ContractService(db)
    contract = await service.update_contract(public_id, dto, actor_email=current_user.email)
    return (await _respond(service, [contract]))[0]


@router.delete("/{public_id}", response_model=ContractResponse)
async def delete_contract(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(HR_ROLES)),
):
    service = ContractService(db)
    contract = await service.delete_contract(public_id, actor_email=current_user.email)
    return (await _respond(service, [contract]))[0]
