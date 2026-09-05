"""
Taskiq task for AI-assisted transfer request decision — built on the generic
AIReviewJob (app/ai/review_job.py). AIDecisionNode.evaluate() calls generate()
DIRECTLY (no second Taskiq hop) since this already runs inside a worker task.

Transfer's AI never finalizes on its own: every decision value maps to the
SAME PENDING_REVIEW status below, because architecture requires a human to
always confirm. Compare with booking_decision.py, where the decision DOES
drive different next-states — status_map is per-entity, not a fixed shape.
"""
import logging
from app.jobs.broker import broker
from app.core.database import AsyncSessionLocal
from app.ai.review_job import AIReviewJob
from app.services.transfer import TransferService
from app.models.enums import TransferStatus

logger = logging.getLogger("harmonix360.jobs.transfer_decision")

TRANSFER_DECISION_PROMPT = """You are an enterprise asset management AI reviewing a transfer request.

Based on the context below, decide whether to APPROVE, ESCALATE (to human review), or REJECT this transfer.

Rules:
- APPROVE if the transfer reason is clear, the asset is in good condition, and the requesting user has a legitimate business need.
- REJECT if the reason is clearly invalid, the asset is not in a transferable state, or there are policy violations.
- ESCALATE if you are uncertain, the asset is high-value (purchase cost > $5000), or the request needs management review.

Respond in EXACTLY this JSON format (no other text):
{{"decision": "approve|escalate|reject", "rationale": "Your 1-2 sentence explanation"}}

Context:
{context}"""

TRANSFER_STATUS_MAP = {
    "approve": TransferStatus.PENDING_REVIEW,
    "escalate": TransferStatus.PENDING_REVIEW,
    "reject": TransferStatus.PENDING_REVIEW,
}


@broker.task(task_name="evaluate_transfer_decision")
async def evaluate_transfer_decision(
    transfer_public_id: str,
    workflow_context: dict,
) -> dict:
    """
    Evaluate a transfer request using the generic AI review job.
    Returns the decision data for polling.
    """
    logger.info("Starting AI decision evaluation for transfer %s", transfer_public_id)

    async with AsyncSessionLocal() as session:
        try:
            job = AIReviewJob(
                entity_service=TransferService(session),
                status_map=TRANSFER_STATUS_MAP,
                prompt_template=TRANSFER_DECISION_PROMPT,
                fallback_decision="escalate",
                required_status=TransferStatus.AI_REVIEWING,
            )
            updated = await job.run(transfer_public_id, workflow_context, task_type="transfer_decision")
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Failed to process AI decision for transfer %s", transfer_public_id)
            raise

    return {
        "transfer_public_id": transfer_public_id,
        "decision": updated.ai_decision_data,
    }
