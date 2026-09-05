"""
Transfer Service — business logic for transfer requests with AI decision nodes.

Architecture ref: Section 5 — "The rationale and the AI's raw output are written
to the immutable audit log alongside the human override (if any). A human can
always override; the override itself is also audit-logged."
"""
import logging
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from app.repositories.transfer import TransferRepository
from app.repositories.asset import AssetRepository
from app.models.entities import TransferRequest
from app.models.enums import TransferStatus, UserRole
from app.schemas.transfer import TransferCreate, TransferOverride
from app.services.base import BaseService

logger = logging.getLogger("harmonix360.services.transfer")


class TransferService(BaseService[TransferRequest]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, TransferRepository(session), entity_name="TransferRequest")
        self.asset_repo = AssetRepository(session)

    async def create_transfer_request(
        self, dto: TransferCreate, actor_email: str, actor_user_id: int = 1
    ) -> TransferRequest:
        """Create a transfer request and transition to PENDING status."""
        # Resolve asset by public_id
        asset = await self.asset_repo.get_by_public_id(dto.asset_public_id)
        if not asset:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Asset '{dto.asset_public_id}' not found",
            )

        transfer = TransferRequest(
            public_id="temp",
            asset_id=asset.id,
            to_user_id=dto.to_user_id,
            requested_by=actor_user_id,
            status=TransferStatus.AI_REVIEWING,  # Go straight to AI review
            reason=dto.reason,
        )
        return await self.create(
            transfer,
            actor=actor_email,
            action="CREATE_TRANSFER",
            after_diff={
                "asset_public_id": dto.asset_public_id,
                "reason": dto.reason,
                "status": TransferStatus.AI_REVIEWING.value,
            },
        )

    # AI review is no longer a hand-written method here — see
    # app/jobs/tasks/transfer_decision.py, which builds a generic
    # AIReviewJob(entity_service=self, ...) and calls .run() directly.

    async def human_override(
        self,
        transfer_public_id: str,
        override_decision: str,
        note: Optional[str],
        actor_email: str,
        actor_role: UserRole = UserRole.ADMIN,
    ) -> TransferRequest:
        """
        Apply a human override to a transfer decision.
        This writes a SEPARATE audit_log entry with action="HUMAN_OVERRIDE"
        (distinct from the AI's original proposal row).
        """
        transfer = await self.get_or_404(transfer_public_id)

        # Allowed from: PENDING_REVIEW, APPROVED, REJECTED
        allowed_states = [
            TransferStatus.PENDING_REVIEW,
            TransferStatus.APPROVED,
            TransferStatus.REJECTED,
        ]
        if transfer.status not in allowed_states:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot override from state '{transfer.status.value}'. Allowed: {[s.value for s in allowed_states]}",
            )

        old_status = transfer.status.value
        old_ai_decision = transfer.ai_decision_data

        if override_decision == "approve":
            transfer.status = TransferStatus.APPROVED
        elif override_decision == "reject":
            transfer.status = TransferStatus.REJECTED
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid override decision '{override_decision}'. Must be 'approve' or 'reject'.",
            )

        transfer.decision_note = note or transfer.decision_note

        updated = await self.update(
            transfer,
            actor=actor_email,
            action="HUMAN_OVERRIDE",
            before_diff={"status": old_status, "ai_decision": old_ai_decision},
            after_diff={"status": transfer.status.value, "override_decision": override_decision, "note": note},
            reason=note,
        )

        logger.info(
            "Human override for transfer %s: %s by %s",
            transfer_public_id, override_decision, actor_email,
        )

        return updated

    async def get_transfer(self, public_id: str) -> TransferRequest:
        return await self.get_or_404(public_id)
