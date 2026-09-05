"""Contract request/response shapes (PS A2)."""
from datetime import date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import ContractStatus
from app.schemas.common import ORMModel
from app.schemas.employee import DepartmentRef, EmployeeRef, WorkingScheduleRef


class SalaryStructureRef(ORMModel):
    """A salary structure as it appears nested in a contract.

    The table is empty until Phase 3 authors the first structure, so this is
    almost always null today — but the shape is here now so Phase 3 adds rows
    rather than a schema change.
    """

    id: str = Field(validation_alias="public_id")
    name: str
    code: str


class ContractBase(BaseModel):
    #: Numeric in the database and Decimal here — Architecture §10. Pydantic
    #: parses a JSON number straight into Decimal, so no float ever exists in
    #: between; `gt=0` because a zero or negative wage is a data-entry error
    #: that would silently produce a zero payslip.
    wage: Decimal = Field(gt=0, max_digits=12, decimal_places=2)
    start_date: date
    #: NULL means open-ended.
    end_date: Optional[date] = None
    job_position: Optional[str] = Field(default=None, max_length=180)
    status: ContractStatus = ContractStatus.DRAFT
    notes: Optional[str] = None

    department_id: Optional[str] = None
    #: FK to a table Phase 3 populates. Accepted now so the form is complete
    #: and Phase 3 adds no migration or schema change here.
    salary_structure_id: Optional[str] = None
    #: Per-contract override of the employee's default schedule (PS A3).
    working_schedule_id: Optional[str] = None

    @model_validator(mode="after")
    def _end_not_before_start(self) -> "ContractBase":
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        return self


class ContractCreate(ContractBase):
    model_config = ConfigDict(extra="forbid")

    employee_id: str


class ContractUpdate(BaseModel):
    """PATCH. `employee_id` is absent deliberately — moving a contract to a
    different employee would silently rewrite two people's payroll history, and
    is not an edit; it is a delete and a create."""

    model_config = ConfigDict(extra="forbid")

    wage: Optional[Decimal] = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    job_position: Optional[str] = Field(default=None, max_length=180)
    status: Optional[ContractStatus] = None
    notes: Optional[str] = None
    department_id: Optional[str] = None
    salary_structure_id: Optional[str] = None
    working_schedule_id: Optional[str] = None


class ContractResponse(ORMModel):
    id: str = Field(validation_alias="public_id")
    wage: Decimal
    start_date: date
    end_date: Optional[date] = None
    job_position: Optional[str] = None
    status: ContractStatus
    notes: Optional[str] = None

    employee: EmployeeRef
    department: Optional[DepartmentRef] = None
    # Nested objects, not raw FK columns. `Contract.working_schedule_id` is an
    # integer primary key; exposing it would both leak a sequential id and hand
    # the client a value it cannot use in any subsequent request — every API
    # here speaks public_ids. It also lets the contract list show the schedule's
    # name and hours without a second round trip.
    salary_structure: Optional[SalaryStructureRef] = None
    working_schedule: Optional[WorkingScheduleRef] = None

    #: SERVER-COMPUTED (PS A2: "the list highlights the active contract").
    #:
    #: True when this is the contract that is active TODAY for its employee.
    #: Computed here rather than derived in the browser from `status` and the
    #: dates, because "active" is a question about the whole set of an
    #: employee's contracts and a paginated list may not contain all of them —
    #: a client-side answer would be confidently wrong on page two.
    is_currently_active: bool = False

    version: int
