"""
Background task: write Notification rows when an Asset changes state.

Triggered from AssetService.transition_asset() via `await task.kiq(...)`.
Never called synchronously.
"""
import uuid
import logging
from app.jobs.broker import broker
from app.core.database import AsyncSessionLocal
from app.models.entities import Notification
from sqlalchemy import select, func
from app.models.entities import User

logger = logging.getLogger("harmonix360.jobs.notifications")


@broker.task(task_name="send_asset_state_change_notification")
async def send_asset_state_change_notification(
    asset_public_id: str,
    old_status: str,
    new_status: str,
    actor_email: str,
) -> dict:
    """
    Write a Notification row for relevant users when an asset changes state.

    For now, notifies all ADMIN and ASSET_MANAGER users. In a real deployment,
    this would filter by department, assignment, etc.
    """
    logger.info(
        "Processing notification: asset=%s %s->%s by %s",
        asset_public_id, old_status, new_status, actor_email,
    )

    async with AsyncSessionLocal() as session:
        # Find admin and asset manager users to notify
        stmt = select(User).where(
            User.role.in_(["ADMIN", "ASSET_MANAGER"]),
            User.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        users = result.scalars().all()

        notifications_created = 0
        for user in users:
            # Don't notify the actor who triggered the change
            if user.email == actor_email:
                continue

            notification = Notification(
                public_id=f"ntf_{uuid.uuid4().hex[:12]}",
                user_id=user.id,
                type="ASSET_STATE_CHANGE",
                title=f"Asset {asset_public_id} status changed",
                message=f"Asset {asset_public_id} transitioned from {old_status} to {new_status} by {actor_email}.",
                metadata_json={
                    "asset_public_id": asset_public_id,
                    "old_status": old_status,
                    "new_status": new_status,
                    "actor": actor_email,
                },
            )
            session.add(notification)
            notifications_created += 1

        await session.commit()

    logger.info(
        "Created %d notifications for asset %s state change",
        notifications_created, asset_public_id,
    )
    return {
        "asset_public_id": asset_public_id,
        "notifications_created": notifications_created,
    }
