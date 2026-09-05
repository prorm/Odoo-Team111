from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, Field
from app.models.enums import AssetStatus, AssetCondition

class AssetBase(BaseModel):
    name: str = Field(..., example="MacBook Pro M3")
    asset_tag: str = Field(..., example="AST-10024")
    serial_number: Optional[str] = None
    category_id: int
    department_id: Optional[int] = None
    condition: AssetCondition = AssetCondition.GOOD
    location: Optional[str] = None
    is_bookable: bool = False
    purchase_date: Optional[datetime] = None
    # Decimal, not float — matches Asset.purchase_cost's Numeric(12, 2) column.
    # Pydantic parses a JSON number or a quoted string into Decimal; keep two
    # decimal places so request and column agree on scale.
    purchase_cost: Optional[Decimal] = Field(default=None, max_digits=12, decimal_places=2)

class AssetCreate(AssetBase):
    pass

class AssetUpdate(BaseModel):
    name: Optional[str] = None
    condition: Optional[AssetCondition] = None
    location: Optional[str] = None
    is_bookable: Optional[bool] = None

class AssetWorkflowTransition(BaseModel):
    action: str = Field(..., example="ALLOCATE")
    reason: Optional[str] = None

class AssetResponse(AssetBase):
    id: str = Field(..., description="Hashids public_id")
    status: AssetStatus
    tenant_id: str
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
