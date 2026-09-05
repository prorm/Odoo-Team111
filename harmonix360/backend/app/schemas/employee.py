"""Employee request/response shapes (PS A1/B2)."""
from datetime import date
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import EmployeeStatus, EmployeeType
from app.schemas.common import ORMModel


class DepartmentRef(ORMModel):
    """A department as it appears nested in another entity."""

    id: str = Field(validation_alias="public_id")
    name: str
    code: str


class EmployeeRef(ORMModel):
    """An employee reduced to what a reference needs: enough to render a chip
    or an option in a manager picker, and nothing more.

    Deliberately excludes bank_account, phone and hire_date. A manager picker
    lists every colleague, so whatever this schema carries is readable by
    anyone who can open the Employee form — which is the whole HR team, and
    later the employee themselves.
    """

    id: str = Field(validation_alias="public_id")
    first_name: str
    last_name: str
    work_email: EmailStr
    job_position: Optional[str] = None
    status: EmployeeStatus


class WorkingScheduleRef(ORMModel):
    id: str = Field(validation_alias="public_id")
    name: str
    weekly_hours: float


class EmployeeBase(BaseModel):
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = Field(min_length=1, max_length=120)
    work_email: EmailStr
    phone: Optional[str] = Field(default=None, max_length=32)
    job_position: Optional[str] = Field(default=None, max_length=180)
    employee_type: EmployeeType = EmployeeType.PERMANENT
    status: EmployeeStatus = EmployeeStatus.ACTIVE
    hire_date: Optional[date] = None
    exit_date: Optional[date] = None
    bank_account: Optional[str] = Field(default=None, max_length=64)

    #: Public ids, never integer primary keys — the client has no access to
    #: those and should not be able to guess a relationship into existence.
    department_id: Optional[str] = None
    manager_id: Optional[str] = None
    default_schedule_id: Optional[str] = None

    @field_validator("first_name", "last_name")
    @classmethod
    def _strip_names(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("work_email")
    @classmethod
    def _lowercase_email(cls, value: str) -> str:
        # Normalised on the way in, because the (work_email, tenant_id) unique
        # constraint is case-SENSITIVE in Postgres: without this,
        # "R.Verma@..." and "r.verma@..." are two employees who are one person.
        return value.lower()


class EmployeeCreate(EmployeeBase):
    """`user_id` is absent on purpose. Linking an Employee to a login is an
    account-management action (Architecture §5 gives it to Admin alone), not
    part of creating an HR record, and accepting it here would let any HR
    Manager attach an employee to an arbitrary account — including one whose
    role outranks their own."""


class EmployeeUpdate(BaseModel):
    """Every field optional: this is a PATCH, and a field the client omits must
    keep its current value rather than being cleared.

    `model_fields_set` distinguishes "omitted" from "explicitly set to null",
    so clearing a manager is expressible without every partial update wiping
    every unmentioned field.
    """

    model_config = ConfigDict(extra="forbid")

    first_name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    last_name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    work_email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=32)
    job_position: Optional[str] = Field(default=None, max_length=180)
    employee_type: Optional[EmployeeType] = None
    status: Optional[EmployeeStatus] = None
    hire_date: Optional[date] = None
    exit_date: Optional[date] = None
    bank_account: Optional[str] = Field(default=None, max_length=64)
    department_id: Optional[str] = None
    manager_id: Optional[str] = None
    default_schedule_id: Optional[str] = None

    @field_validator("work_email")
    @classmethod
    def _lowercase_email(cls, value: Optional[str]) -> Optional[str]:
        return value.lower() if value else value


class EmployeeResponse(ORMModel):
    id: str = Field(validation_alias="public_id")
    first_name: str
    last_name: str
    full_name: str
    work_email: EmailStr
    phone: Optional[str] = None
    job_position: Optional[str] = None
    employee_type: EmployeeType
    status: EmployeeStatus
    hire_date: Optional[date] = None
    exit_date: Optional[date] = None
    bank_account: Optional[str] = None

    department: Optional[DepartmentRef] = None
    manager: Optional[EmployeeRef] = None
    default_schedule: Optional[WorkingScheduleRef] = None

    #: Optimistic-concurrency token. The client sends it back on update; a
    #: mismatch is a 409 rather than a silent overwrite of someone else's edit.
    version: int


class SmartButtonCounts(BaseModel):
    """Counts behind the Employee form's smart buttons (PS B2).

    One endpoint returning all four rather than four endpoints: the form
    renders them together, and four round-trips to draw four numbers is three
    too many. Counts the employee's own related records only.
    """

    contracts: int
    attendance: int
    time_off_requests: int
    time_off_allocations: int
