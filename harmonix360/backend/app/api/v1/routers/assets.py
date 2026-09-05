from typing import List
from fastapi import APIRouter, Depends, status, Query, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.rate_limit import rate_limiter
from app.api.v1.deps import get_current_user, require_role, CurrentUser
from app.models.enums import UserRole
from app.schemas.asset import AssetCreate, AssetResponse, AssetWorkflowTransition
from app.schemas.common import ResponseEnvelope, PaginatedResponse
from app.services.asset import AssetService

router = APIRouter(prefix="/assets", tags=["Assets"], dependencies=[Depends(rate_limiter)])

@router.post("/", response_model=AssetResponse, status_code=status.HTTP_201_CREATED)
async def create_asset(
    dto: AssetCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(UserRole.ASSET_MANAGER, UserRole.ADMIN)),
    idempotency_key: str = Header(None, alias="Idempotency-Key")
):
    service = AssetService(db)
    asset = await service.create_asset(dto, actor_email=current_user.email)
    return AssetResponse(
        id=asset.public_id,
        name=asset.name,
        asset_tag=asset.asset_tag,
        serial_number=asset.serial_number,
        category_id=asset.category_id,
        department_id=asset.department_id,
        status=asset.status,
        condition=asset.condition,
        location=asset.location,
        is_bookable=asset.is_bookable,
        purchase_date=asset.purchase_date,
        purchase_cost=asset.purchase_cost,
        tenant_id=asset.tenant_id,
        created_at=asset.created_at,
        updated_at=asset.updated_at
    )

@router.get("/", response_model=PaginatedResponse[AssetResponse])
async def list_assets(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    service = AssetService(db)
    assets, total = await service.list_assets(limit=limit, offset=offset)
    items = [
        AssetResponse(
            id=a.public_id,
            name=a.name,
            asset_tag=a.asset_tag,
            serial_number=a.serial_number,
            category_id=a.category_id,
            department_id=a.department_id,
            status=a.status,
            condition=a.condition,
            location=a.location,
            is_bookable=a.is_bookable,
            purchase_date=a.purchase_date,
            purchase_cost=a.purchase_cost,
            tenant_id=a.tenant_id,
            created_at=a.created_at,
            updated_at=a.updated_at
        ) for a in assets
    ]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)

@router.get("/{public_id}", response_model=AssetResponse)
async def get_asset(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    service = AssetService(db)
    asset = await service.get_asset(public_id)
    return AssetResponse(
        id=asset.public_id,
        name=asset.name,
        asset_tag=asset.asset_tag,
        serial_number=asset.serial_number,
        category_id=asset.category_id,
        department_id=asset.department_id,
        status=asset.status,
        condition=asset.condition,
        location=asset.location,
        is_bookable=asset.is_bookable,
        purchase_date=asset.purchase_date,
        purchase_cost=asset.purchase_cost,
        tenant_id=asset.tenant_id,
        created_at=asset.created_at,
        updated_at=asset.updated_at
    )

@router.post("/{public_id}/transition", response_model=AssetResponse)
async def transition_asset(
    public_id: str,
    dto: AssetWorkflowTransition,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    service = AssetService(db)
    asset = await service.transition_asset(
        public_id=public_id,
        action=dto.action,
        actor_email=current_user.email,
        actor_role=current_user.role,
        reason=dto.reason
    )
    return AssetResponse(
        id=asset.public_id,
        name=asset.name,
        asset_tag=asset.asset_tag,
        serial_number=asset.serial_number,
        category_id=asset.category_id,
        department_id=asset.department_id,
        status=asset.status,
        condition=asset.condition,
        location=asset.location,
        is_bookable=asset.is_bookable,
        purchase_date=asset.purchase_date,
        purchase_cost=asset.purchase_cost,
        tenant_id=asset.tenant_id,
        created_at=asset.created_at,
        updated_at=asset.updated_at
    )

@router.get("/debug/trigger-error")
async def trigger_sentry_error():
    """Trigger an unhandled exception for Sentry exception tracking verification (dev/debug only)."""
    from app.core.config import settings
    if settings.ENVIRONMENT.lower() == "production":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    raise ValueError("Sentry exception tracking test: Asset database query failed for bogus_id")

