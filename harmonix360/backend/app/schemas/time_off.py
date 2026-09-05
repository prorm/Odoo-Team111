from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import TimeOffAllocationStatus, TimeOffUnit
from app.schemas.employee import EmployeeRef
from app.schemas.hr import (
    TimeOffAllocationSummary,
    TimeOffRequestSummary,
    TimeOffTypeSummary,
)


class VersionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class TypeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=180)
    code: str = Field(min_length=1, max_length=64)
    unit: TimeOffUnit = TimeOffUnit.DAYS
    requires_allocation: bool = True
    requires_approval: bool = True
    payroll_integration: bool = False
    description: str | None = None


class TypeUpdate(TypeCreate, VersionInput):
    pass


class TypeResponse(TimeOffTypeSummary):
    description: str | None = None


class AllocationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: str
    time_off_type_id: str
    allocated: Decimal = Field(gt=0, max_digits=8, decimal_places=2)
    valid_from: date
    valid_to: date | None = None
    status: TimeOffAllocationStatus = TimeOffAllocationStatus.CONFIRMED


class AllocationUpdate(VersionInput):
    allocated: Decimal = Field(gt=0, max_digits=8, decimal_places=2)
    valid_from: date
    valid_to: date | None = None
    status: TimeOffAllocationStatus


class AllocationResponse(TimeOffAllocationSummary):
    employee: EmployeeRef
    time_off_type: TypeResponse


class RequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: str
    time_off_type_id: str
    date_from: date
    date_to: date
    reason: str | None = Field(default=None, max_length=2000)


class RequestUpdate(RequestCreate, VersionInput):
    pass


class Decision(VersionInput):
    decision_note: str | None = Field(default=None, max_length=2000)


class RequestResponse(TimeOffRequestSummary):
    employee: EmployeeRef
    time_off_type: TypeResponse
    reason: str | None = None
    decision_note: str | None = None
    approved_by: str | None = None
    allocation_id: str | None = None
