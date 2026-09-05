from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.schemas.employee import EmployeeRef
from app.schemas.hr import AttendanceSummary


class AttendanceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    employee_id: str
    check_in: AwareDatetime
    check_out: AwareDatetime | None = None


class AttendanceCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)
    check_in: AwareDatetime
    check_out: AwareDatetime | None = None
    correction_reason: str = Field(min_length=1, max_length=2000)


class AttendanceCheckout(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class AttendanceResponse(AttendanceSummary):
    employee: EmployeeRef
    correction_reason: str | None = None
    corrected_by: str | None = None
