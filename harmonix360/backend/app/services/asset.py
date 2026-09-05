import logging
from typing import Optional, List, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from app.repositories.asset import AssetRepository
from app.models.entities import Asset
from app.models.enums import AssetStatus, UserRole
from app.schemas.asset import AssetCreate, AssetUpdate
from app.workflows.engine import WorkflowEngine
from app.workflows.asset_workflow import asset_workflow
from app.services.base import BaseService

logger = logging.getLogger("harmonix360.services.asset")


class AssetService(BaseService[Asset]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, AssetRepository(session), entity_name="Asset")

    async def create_asset(self, dto: AssetCreate, actor_email: str) -> Asset:
        existing = await self.repo.get_by_asset_tag(dto.asset_tag)
        if existing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Asset tag '{dto.asset_tag}' already exists")

        asset = Asset(
            public_id="temp",
            name=dto.name,
            asset_tag=dto.asset_tag,
            serial_number=dto.serial_number,
            category_id=dto.category_id,
            department_id=dto.department_id,
            status=AssetStatus.AVAILABLE,
            condition=dto.condition,
            location=dto.location,
            is_bookable=dto.is_bookable,
            purchase_date=dto.purchase_date,
            purchase_cost=dto.purchase_cost
        )
        return await self.create(
            asset,
            actor=actor_email,
            action="CREATE_ASSET",
            after_diff={"name": asset.name, "tag": asset.asset_tag, "status": asset.status.value},
        )

    async def get_asset(self, public_id: str) -> Asset:
        return await self.get_or_404(public_id)

    async def list_assets(self, limit: int = 50, offset: int = 0) -> Tuple[List[Asset], int]:
        return await self.list(limit, offset)

    async def transition_asset(self, public_id: str, action: str, actor_email: str, actor_role: UserRole, reason: Optional[str] = None) -> Asset:
        asset = await self.get_asset(public_id)
        old_status = asset.status.value

        new_status_str = WorkflowEngine.validate_transition(
            definition=asset_workflow,
            current_state=old_status,
            action=action,
            user_role=actor_role
        )

        asset.status = AssetStatus(new_status_str)
        updated_asset = await self.update(
            asset,
            actor=actor_email,
            action=f"TRANSITION_{action}",
            before_diff={"status": old_status},
            after_diff={"status": new_status_str},
            reason=reason,
        )

        # Enqueue notification job via Taskiq (never called synchronously)
        try:
            from app.jobs.tasks.notifications import send_asset_state_change_notification
            await send_asset_state_change_notification.kiq(
                asset_public_id=updated_asset.public_id,
                old_status=old_status,
                new_status=new_status_str,
                actor_email=actor_email,
            )
            logger.info("Enqueued notification for asset %s transition %s->%s", updated_asset.public_id, old_status, new_status_str)
        except Exception:
            # Notification failure must not block the transition itself
            logger.exception("Failed to enqueue notification for asset %s", updated_asset.public_id)

        return updated_asset
