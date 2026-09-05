"""
Generic Booking Service — books any resource_type registered in
app/services/resource_registry.py. No Asset-specific logic lives here;
app/services/booking_resources.py is what wires "asset" and "meeting_room"
into the registry.
"""
import logging
from datetime import datetime
from typing import Optional
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from app.repositories.booking import BookingRepository
from app.models.entities import ResourceBooking
from app.models.enums import BookingStatus
from app.services.base import BaseService
from app.services import resource_registry
# Import for its registration side-effect: populates resource_registry with
# the resource types this deployment of Harmonix360 actually knows about.
from app.services import booking_resources  # noqa: F401

logger = logging.getLogger("harmonix360.services.booking")

# Postgres SQLSTATE for exclusion_violation (what resource_bookings_range_overlap_excl raises).
EXCLUSION_VIOLATION_SQLSTATE = "23P01"


class BookingService(BaseService[ResourceBooking]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, BookingRepository(session), entity_name="ResourceBooking")

    async def _resolve_resource(self, resource_type: str, resource_public_id: str) -> int:
        """Look up a resource via the registry. Returns its internal id, or
        raises 404/400 if the type is unknown, the resource doesn't exist, or
        it isn't bookable."""
        resolver = resource_registry.get_resolver(resource_type)
        if resolver is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown resource_type '{resource_type}'. Registered types: "
                       f"{resource_registry.registered_resource_types()}",
            )

        resolved = await resolver(self.session, resource_public_id)
        if resolved is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Resource '{resource_public_id}' of type '{resource_type}' not found",
            )

        resource_id, is_bookable = resolved
        if not is_bookable:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Resource '{resource_public_id}' is not bookable",
            )
        return resource_id

    async def _create_booking(
        self,
        *,
        resource_type: str,
        resource_id: int,
        start_time: datetime,
        end_time: datetime,
        status_value: BookingStatus,
        user_id: int,
        actor_email: str,
        purpose: str,
        action: str,
        resource_public_id: str,
    ) -> ResourceBooking:
        booking = ResourceBooking(
            public_id="temp",
            resource_type=resource_type,
            resource_id=resource_id,
            user_id=user_id,
            start_time=start_time,
            end_time=end_time,
            status=status_value,
            purpose=purpose,
        )
        try:
            return await self.create(
                booking,
                actor=actor_email,
                action=action,
                after_diff={
                    "resource_type": resource_type,
                    "resource_public_id": resource_public_id,
                    "start_time": str(start_time),
                    "end_time": str(end_time),
                },
            )
        except IntegrityError as exc:
            await self.session.rollback()
            if getattr(exc.orig, "sqlstate", None) == EXCLUSION_VIOLATION_SQLSTATE:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Resource '{resource_public_id}' is already booked for an overlapping time range",
                ) from exc
            raise

    async def create_booking(
        self,
        resource_type: str,
        resource_public_id: str,
        start_time: datetime,
        end_time: datetime,
        user_id: int = 1,
        actor_email: str = "ai-agent",
        purpose: str = "MCP-created booking",
    ) -> ResourceBooking:
        """Create a resource booking. Postgres EXCLUDE constraint handles double-booking;
        IntegrityError from it is translated to HTTP 409 above."""
        resource_id = await self._resolve_resource(resource_type, resource_public_id)
        return await self._create_booking(
            resource_type=resource_type,
            resource_id=resource_id,
            start_time=start_time,
            end_time=end_time,
            status_value=BookingStatus.CONFIRMED,
            user_id=user_id,
            actor_email=actor_email,
            purpose=purpose,
            action="CREATE_BOOKING",
            resource_public_id=resource_public_id,
        )

    async def create_booking_for_review(
        self,
        resource_type: str,
        resource_public_id: str,
        start_time: datetime,
        end_time: datetime,
        user_id: int = 1,
        actor_email: str = "requester",
        purpose: str = "AI-reviewed booking request",
    ) -> ResourceBooking:
        """
        Create a booking request that starts PENDING instead of auto-confirming —
        the AI-review demo path (app/jobs/tasks/booking_decision.py), separate from
        create_booking()'s existing synchronous-confirm contract used by the MCP tool.
        """
        resource_id = await self._resolve_resource(resource_type, resource_public_id)
        return await self._create_booking(
            resource_type=resource_type,
            resource_id=resource_id,
            start_time=start_time,
            end_time=end_time,
            status_value=BookingStatus.PENDING,
            user_id=user_id,
            actor_email=actor_email,
            purpose=purpose,
            action="CREATE_BOOKING_REQUEST",
            resource_public_id=resource_public_id,
        )

    async def override_booking(
        self,
        booking_public_id: str,
        override_decision: str,
        note: Optional[str],
        actor_email: str,
    ) -> ResourceBooking:
        """Human override of a booking decision — a human can always override, mirroring
        TransferService.human_override. Writes a SEPARATE audit_log entry ("HUMAN_OVERRIDE")
        from the AI's original proposal row."""
        booking = await self.get_or_404(booking_public_id)

        allowed_states = [BookingStatus.PENDING, BookingStatus.CONFIRMED, BookingStatus.CANCELLED]
        if booking.status not in allowed_states:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot override from state '{booking.status.value}'. Allowed: {[s.value for s in allowed_states]}",
            )

        old_status = booking.status.value

        if override_decision == "grant":
            booking.status = BookingStatus.CONFIRMED
        elif override_decision == "deny":
            booking.status = BookingStatus.CANCELLED
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid override decision '{override_decision}'. Must be 'grant' or 'deny'.",
            )

        try:
            return await self.update(
                booking,
                actor=actor_email,
                action="HUMAN_OVERRIDE",
                before_diff={"status": old_status},
                after_diff={"status": booking.status.value, "override_decision": override_decision, "note": note},
                reason=note,
            )
        except IntegrityError as exc:
            await self.session.rollback()
            if getattr(exc.orig, "sqlstate", None) == EXCLUSION_VIOLATION_SQLSTATE:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Granting this booking would overlap another confirmed booking for the same resource",
                ) from exc
            raise
