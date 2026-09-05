"""
Transfer Request router.

POST /api/v1/transfers/              — create request, enqueue AI decision, return 202
GET  /api/v1/transfers/{public_id}   — get transfer with current status + AI data
POST /api/v1/transfers/{public_id}/override — human override (audit-logged separately)
GET  /api/v1/transfers/{public_id}/decision — poll AI decision task result
"""
from fastapi import APIRouter, Depends, status, Header
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.rate_limit import rate_limiter
from app.api.v1.deps import get_current_user, require_role, CurrentUser
from app.models.enums import UserRole
from app.schemas.transfer import (
    TransferCreate,
    TransferResponse,
    TransferOverride,
    TransferCreateResponse,
)
from app.services.transfer import TransferService

router = APIRouter(prefix="/transfers", tags=["Transfers"], dependencies=[Depends(rate_limiter)])


def _to_response(t) -> TransferResponse:
    return TransferResponse(
        id=t.public_id,
        asset_id=t.asset_id,
        from_user_id=t.from_user_id,
        to_user_id=t.to_user_id,
        requested_by=t.requested_by,
        approved_by=t.approved_by,
        status=t.status,
        reason=t.reason,
        decision_note=t.decision_note,
        ai_decision_data=t.ai_decision_data,
        tenant_id=t.tenant_id,
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


@router.post("/", response_model=TransferCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_transfer(
    dto: TransferCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    idempotency_key: str = Header(None, alias="Idempotency-Key"),
):
    """
    Create a transfer request and enqueue AI decision evaluation.
    Returns 202 with the transfer and job_id for polling.
    """
    service = TransferService(db)
    transfer = await service.create_transfer_request(
        dto=dto,
        actor_email=current_user.email,
    )

    # Enqueue AI decision task
    from app.jobs.tasks.transfer_decision import evaluate_transfer_decision

    # Build workflow context for the AI decision node
    from app.repositories.asset import AssetRepository
    asset_repo = AssetRepository(db)
    asset = await asset_repo.get_by_public_id(dto.asset_public_id)

    workflow_context = {
        "transfer_public_id": transfer.public_id,
        "asset_id": transfer.asset_id,
        "asset_name": asset.name if asset else "Unknown",
        # Serialized as a string, not a float: purchase_cost is Decimal
        # (Numeric(12, 2)) and Taskiq JSON-encodes this dict onto Redis, where
        # Decimal is not an encodable type. str() keeps the exact value —
        # float() here would reintroduce the drift Numeric exists to avoid, in
        # the one place money leaves the database.
        "purchase_cost": str(asset.purchase_cost) if asset and asset.purchase_cost is not None else "0.00",
        "reason": dto.reason,
        "requested_by": current_user.email,
        "to_user_id": dto.to_user_id,
    }

    task_handle = await evaluate_transfer_decision.kiq(
        transfer_public_id=transfer.public_id,
        workflow_context=workflow_context,
    )

    return TransferCreateResponse(
        transfer=_to_response(transfer),
        ai_job_id=task_handle.task_id,
    )


@router.get("/{public_id}", response_model=TransferResponse)
async def get_transfer(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get a transfer request with current status and AI decision data."""
    service = TransferService(db)
    transfer = await service.get_transfer(public_id)
    return _to_response(transfer)


@router.post("/{public_id}/override", response_model=TransferResponse)
async def override_transfer(
    public_id: str,
    dto: TransferOverride,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(UserRole.ASSET_MANAGER, UserRole.ADMIN)),
):
    """
    Human override of an AI transfer decision.
    Writes a SEPARATE audit_log entry from the AI's original proposal.
    """
    service = TransferService(db)
    transfer = await service.human_override(
        transfer_public_id=public_id,
        override_decision=dto.decision,
        note=dto.note,
        actor_email=current_user.email,
        actor_role=current_user.role,
    )
    return _to_response(transfer)


@router.get("/{public_id}/decision")
async def get_transfer_decision(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Get the AI decision data for a transfer request."""
    service = TransferService(db)
    transfer = await service.get_transfer(public_id)

    if transfer.ai_decision_data:
        return {
            "transfer_public_id": transfer.public_id,
            "status": transfer.status.value,
            "decision": transfer.ai_decision_data,
        }
    else:
        return {
            "transfer_public_id": transfer.public_id,
            "status": transfer.status.value,
            "decision": None,
            "message": "AI decision still pending.",
        }
