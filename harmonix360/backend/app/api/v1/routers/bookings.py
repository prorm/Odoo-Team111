from fastapi import APIRouter, Depends, status, Header
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.rate_limit import rate_limiter
from app.api.v1.deps import get_current_user, CurrentUser
from app.schemas.booking import BookingCreate, BookingResponse, BookingOverride
from app.services.booking import BookingService

router = APIRouter(prefix="/bookings", tags=["Bookings"], dependencies=[Depends(rate_limiter)])


def _to_response(booking) -> BookingResponse:
    return BookingResponse(
        id=booking.public_id,
        resource_type=booking.resource_type,
        resource_id=booking.resource_id,
        status=booking.status,
        start_time=booking.start_time,
        end_time=booking.end_time,
        purpose=booking.purpose,
        tenant_id=booking.tenant_id,
        created_at=booking.created_at,
        updated_at=booking.updated_at,
    )


@router.post("/", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
async def create_booking(
    dto: BookingCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    idempotency_key: str = Header(None, alias="Idempotency-Key"),
):
    service = BookingService(db)
    booking = await service.create_booking(
        resource_type=dto.resource_type,
        resource_public_id=dto.resource_public_id,
        start_time=dto.start_time,
        end_time=dto.end_time,
        actor_email=current_user.email,
        purpose=dto.purpose or "API-created booking",
    )
    return _to_response(booking)


@router.post("/{booking_public_id}/override", response_model=BookingResponse)
async def override_booking(
    booking_public_id: str,
    dto: BookingOverride,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    service = BookingService(db)
    booking = await service.override_booking(
        booking_public_id=booking_public_id,
        override_decision=dto.decision,
        note=dto.note,
        actor_email=current_user.email,
    )
    return _to_response(booking)
