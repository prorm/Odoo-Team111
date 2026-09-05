"""
Live proof that AIReviewJob (app/ai/review_job.py) is genuinely generic, not
Transfer-specific plumbing wearing a thin disguise.

Wires the SAME generic job to ResourceBooking (a second entity) via
app/jobs/tasks/booking_decision.py, which uses:
  - a different decision vocabulary   (grant/waitlist/deny, not approve/escalate/reject)
  - a different status_map shape      (decision DRIVES the next state, not a constant)
  - no decision_note/ai_decision_data columns (note_field/decision_data_field=None)

Then runs the same three-row audit pattern TransferRequest gets:
  CREATE_BOOKING_REQUEST -> AI_DECISION_PROPOSE (or a fallback variant) -> HUMAN_OVERRIDE
"""
import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.services.booking import BookingService
from app.repositories.asset import AssetRepository
from app.jobs.tasks.booking_decision import evaluate_booking_decision
from app.models.entities import AuditLog


async def main():
    async with AsyncSessionLocal() as db:
        asset_repo = AssetRepository(db)
        res = await db.execute(select(AssetRepository.model).where(AssetRepository.model.is_bookable == True).limit(1))
        asset = res.scalar_one()
        print(f"using asset public_id={asset.public_id!r} name={asset.name!r}")

        print("\n" + "=" * 80)
        print("1. CREATE — BookingService.create_booking_for_review (status starts PENDING)")
        print("=" * 80)
        booking_svc = BookingService(db)
        start = datetime.now(timezone.utc) + timedelta(days=2)
        end = start + timedelta(hours=3)
        booking = await booking_svc.create_booking_for_review(
            resource_type="asset",
            resource_public_id=asset.public_id,
            start_time=start,
            end_time=end,
            actor_email="requester@harmonix360.com",
            purpose="Quarterly planning workshop — need the room for a client walkthrough",
        )
        await db.commit()
        print(f"booking public_id={booking.public_id!r} status={booking.status.value}")
        assert booking.public_id.startswith("bkg_")
        assert booking.status.value == "PENDING"

    print("\n" + "=" * 80)
    print("2. AI REVIEW — evaluate_booking_decision(...) called directly (Taskiq __call__ "
          "runs the original coroutine in-process, no broker needed), which builds a generic "
          "AIReviewJob(entity_service=BookingService(...), status_map=BOOKING_STATUS_MAP, "
          "prompt_template=BOOKING_DECISION_PROMPT, fallback_decision='waitlist') and calls .run()")
    print("=" * 80)
    workflow_context = {
        "booking_public_id": booking.public_id,
        "asset_name": asset.name,
        "asset_id": asset.id,
        "purpose": "Quarterly planning workshop — need the room for a client walkthrough",
        "start_time": str(start),
        "end_time": str(end),
        "requested_by": "requester@harmonix360.com",
    }
    result = await evaluate_booking_decision(booking_public_id=booking.public_id, workflow_context=workflow_context)
    print(f"task result: {result}")

    print("\n" + "=" * 80)
    print("3. HUMAN OVERRIDE — BookingService.override_booking (human always has final say, "
          "regardless of what the AI decided)")
    print("=" * 80)
    async with AsyncSessionLocal() as db:
        booking_svc = BookingService(db)
        overridden = await booking_svc.override_booking(
            booking_public_id=booking.public_id,
            override_decision="grant",
            note="Confirmed room availability manually — approving regardless of AI decision.",
            actor_email="facilities.manager@harmonix360.com",
        )
        await db.commit()
        print(f"booking public_id={overridden.public_id!r} final status={overridden.status.value}")
        assert overridden.status.value == "CONFIRMED"

        print("\n" + "=" * 80)
        print("4. AUDIT TRAIL — three rows for entity='ResourceBooking', entity_id=booking.public_id")
        print("=" * 80)
        res = await db.execute(
            select(AuditLog)
            .where(AuditLog.entity == "ResourceBooking", AuditLog.entity_id == booking.public_id)
            .order_by(AuditLog.id.asc())
        )
        rows = res.scalars().all()
        for row in rows:
            print(f"id={row.id} action={row.action!r} actor={row.actor!r}")
            print(f"   before_diff={row.before_diff}")
            print(f"   after_diff={row.after_diff}")
        actions = [r.action for r in rows]
        print(f"\naction sequence: {actions}")
        assert actions[0] == "CREATE_BOOKING_REQUEST"
        assert actions[1] in ("AI_DECISION_PROPOSE", "AI_DECISION_UNAVAILABLE", "AI_DECISION_PARSE_ERROR")
        assert actions[2] == "HUMAN_OVERRIDE"

        print("\n" + "=" * 80)
        print("DONE — same three-row audit shape as TransferRequest, different entity, "
              "different vocabulary, different status_map, zero copy-pasted service/task code.")
        print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
