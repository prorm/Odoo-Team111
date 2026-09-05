"""Read schemas for the Phase 0 HR skeleton.

One module rather than one per entity, on purpose: at this point every schema
is the same three or four fields, and thirteen near-identical files would hide
the one thing worth checking — that no `id` here is an integer primary key.

Phase 1 splits Employee, Contract and WorkingSchedule out into their own
modules with full create/update/response shapes; Phases 2-5 do the same for the
rest as each entity gets real behaviour. Until then these exist so every router
has a typed, documented response instead of a bare dict.

The invariant that must survive that split: `id` is always the hashid
`public_id`, never `Employee.id`. The integer primary key is sequential, so
exposing it would let anyone count the workforce and address rows they were
never given.
"""
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import Field

from app.models.enums import (
    AttendanceStatus,
    ContractStatus,
    EmployeeStatus,
    EmployeeType,
    PayrunStatus,
    PayslipStatus,
    SalaryRuleCategory,
    SalaryRuleComputation,
    TimeOffAllocationStatus,
    TimeOffRequestStatus,
    TimeOffUnit,
    WorkingScheduleType,
)
from app.schemas.common import ORMModel


class EmployeeSummary(ORMModel):
    id: str = Field(validation_alias="public_id")
    first_name: str
    last_name: str
    work_email: str
    job_position: Optional[str] = None
    employee_type: EmployeeType
    status: EmployeeStatus
    version: int


class ContractSummary(ORMModel):
    id: str = Field(validation_alias="public_id")
    job_position: Optional[str] = None
    wage: Decimal
    start_date: date
    end_date: Optional[date] = None
    status: ContractStatus
    version: int


class WorkingScheduleSummary(ORMModel):
    id: str = Field(validation_alias="public_id")
    name: str
    schedule_type: WorkingScheduleType
    #: Server-computed from the schedule's lines; never client-submitted (PS A3).
    weekly_hours: Decimal
    version: int


class AttendanceSummary(ORMModel):
    id: str = Field(validation_alias="public_id")
    check_in: datetime
    check_out: Optional[datetime] = None
    worked_hours: Optional[Decimal] = None
    status: AttendanceStatus
    version: int


class TimeOffTypeSummary(ORMModel):
    id: str = Field(validation_alias="public_id")
    name: str
    code: str
    unit: TimeOffUnit
    requires_allocation: bool
    requires_approval: bool
    payroll_integration: bool
    version: int


class TimeOffAllocationSummary(ORMModel):
    id: str = Field(validation_alias="public_id")
    allocated: Decimal
    taken: Decimal
    #: Derived (`allocated - taken`), never stored — see app/models/time_off.py.
    remaining: Decimal
    valid_from: date
    valid_to: Optional[date] = None
    status: TimeOffAllocationStatus
    version: int


class TimeOffRequestSummary(ORMModel):
    id: str = Field(validation_alias="public_id")
    date_from: date
    date_to: Optional[date] = None
    duration: Decimal
    status: TimeOffRequestStatus
    version: int


class SalaryStructureSummary(ORMModel):
    id: str = Field(validation_alias="public_id")
    name: str
    code: str
    is_active: bool
    version: int


class SalaryRuleSummary(ORMModel):
    id: str = Field(validation_alias="public_id")
    name: str
    code: str
    category: SalaryRuleCategory
    sequence: int
    computation_method: SalaryRuleComputation
    amount: Optional[Decimal] = None
    is_active: bool
    version: int


class PayrunSummary(ORMModel):
    id: str = Field(validation_alias="public_id")
    name: str
    period_start: date
    period_end: date
    status: PayrunStatus
    version: int


class PayslipSummary(ORMModel):
    id: str = Field(validation_alias="public_id")
    worked_days: Decimal
    gross_amount: Decimal
    net_amount: Decimal
    status: PayslipStatus
    version: int
