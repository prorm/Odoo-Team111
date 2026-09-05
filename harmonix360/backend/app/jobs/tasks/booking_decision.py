"""
Taskiq task for AI-assisted resource-booking decisions — second entity wired
onto the generic AIReviewJob (app/ai/review_job.py), proving it's not
Transfer-specific plumbing wearing a thin disguise.

Deliberately a DIFFERENT decision vocabulary from transfer_decision.py
(grant / waitlist / deny, not approve / escalate / reject) and a DIFFERENT
status_map shape: each decision drives a genuinely different ResourceBooking
status, instead of transfer's "every decision maps to the same PENDING_REVIEW".

ResourceBooking has no decision_note/ai_decision_data columns (unlike
TransferRequest) — note_field/decision_data_field are set to None below;
AIReviewJob only writes the decision into the audit log's after_diff.
"""
import logging
from app.jobs.broker import broker
from app.core.database import AsyncSessionLocal
from app.ai.review_job import AIReviewJob
from app.services.booking import BookingService
from app.models.enums import BookingStatus

logger = logging.getLogger("harmonix360.jobs.booking_decision")

BOOKING_DECISION_PROMPT = """You are a facilities AI reviewing a shared-resource booking request.

Based on the context below, decide whether to GRANT, WAITLIST, or DENY this booking.

Rules:
- GRANT if the requested window is clearly justified and the resource is a routine, low-contention asset.
- DENY if the purpose is missing/invalid, or the request conflicts with a stated maintenance/blackout window.
- WAITLIST if you are uncertain, the resource is high-demand, or the request needs a human to confirm availability.

Respond in EXACTLY this JSON format (no other text):
{{"decision": "grant|waitlist|deny", "rationale": "Your 1-2 sentence explanation"}}

Context:
{context}"""

BOOKING_STATUS_MAP = {
    "grant": BookingStatus.CONFIRMED,
    "waitlist": BookingStatus.PENDING,
    "deny": BookingStatus.CANCELLED,
}


@broker.task(task_name="evaluate_booking_decision")
async def evaluate_booking_decision(
    booking_public_id: str,
    workflow_context: dict,
) -> dict:
    """Evaluate a booking request using the generic AI review job."""
    logger.info("Starting AI decision evaluation for booking %s", booking_public_id)

    async with AsyncSessionLocal() as session:
        try:
            job = AIReviewJob(
                entity_service=BookingService(session),
                status_map=BOOKING_STATUS_MAP,
                prompt_template=BOOKING_DECISION_PROMPT,
                fallback_decision="waitlist",
                required_status=BookingStatus.PENDING,
                note_field=None,           # ResourceBooking has no decision_note column
                decision_data_field=None,  # ...nor ai_decision_data; audit log carries it
            )
            updated = await job.run(booking_public_id, workflow_context, task_type="booking_decision")
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception("Failed to process AI decision for booking %s", booking_public_id)
            raise

    return {
        "booking_public_id": booking_public_id,
        "status": updated.status.value,
    }
