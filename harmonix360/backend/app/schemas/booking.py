from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from app.models.enums import BookingStatus


class BookingCreate(BaseModel):
    resource_type: str = Field(..., example="asset", description="Registered resource type, e.g. 'asset' or 'meeting_room'")
    resource_public_id: str = Field(..., example="ast_g8mL2wz1")
    start_time: datetime
    end_time: datetime
    purpose: Optional[str] = None


class BookingOverride(BaseModel):
    decision: str = Field(..., example="grant", description="'grant' or 'deny'")
    note: Optional[str] = None


class BookingResponse(BaseModel):
    id: str = Field(..., description="Hashids public_id")
    resource_type: str
    resource_id: int
    status: BookingStatus
    start_time: datetime
    end_time: datetime
    purpose: Optional[str] = None
    tenant_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
