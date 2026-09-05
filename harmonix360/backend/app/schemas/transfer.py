"""
Transfer Request Pydantic schemas.
"""
from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field
from app.models.enums import TransferStatus


class TransferCreate(BaseModel):
    asset_public_id: str = Field(..., description="Public ID of the asset to transfer")
    to_user_id: Optional[int] = Field(None, description="Target user internal ID (optional)")
    reason: str = Field(..., description="Business reason for the transfer")


class TransferOverride(BaseModel):
    decision: Literal["approve", "reject"] = Field(..., description="Human override decision")
    note: Optional[str] = Field(None, description="Optional note from the human reviewer")


class TransferAIDecisionResponse(BaseModel):
    decision: str
    status: str
    rationale: str
    provider: str
    raw_output: Optional[str] = None


class TransferResponse(BaseModel):
    id: str = Field(..., description="Hashids public_id")
    asset_id: int
    from_user_id: Optional[int] = None
    to_user_id: Optional[int] = None
    requested_by: int
    approved_by: Optional[int] = None
    status: TransferStatus
    reason: Optional[str] = None
    decision_note: Optional[str] = None
    ai_decision_data: Optional[dict] = None
    tenant_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TransferCreateResponse(BaseModel):
    """Response for transfer creation — includes job_id for AI decision polling."""
    transfer: TransferResponse
    ai_job_id: str
    message: str = "Transfer request created. AI decision in progress."
