"""Working schedule request/response shapes (PS A3).

The rule this file enforces at the type level: **weekly hours are computed
server-side and are never client-submitted.** `weekly_hours` appears on the
response schema and on NEITHER request schema, so there is no shape a client
can send that carries it. That is the strongest available guarantee — stronger
than validating it away, because there is nothing to validate.
"""
from datetime import time
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import Weekday, WorkingScheduleType
from app.schemas.common import ORMModel


class ScheduleLineInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day_of_week: Weekday
    start_time: time
    end_time: time
    #: Unpaid break inside this block. Minutes because that is how people enter
    #: it ("45 min"), and an integer cannot accumulate a rounding error.
    break_minutes: int = Field(default=0, ge=0, le=24 * 60)

    @model_validator(mode="after")
    def _end_after_start(self) -> "ScheduleLineInput":
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        worked_minutes = (
            self.end_time.hour * 60 + self.end_time.minute
        ) - (self.start_time.hour * 60 + self.start_time.minute)
        if self.break_minutes >= worked_minutes:
            # A break at least as long as the block would make the line
            # contribute zero or negative hours, which is either a typo or an
            # attempt to drag a schedule's total down. Either way it is not a
            # working block.
            raise ValueError("break_minutes must be shorter than the block itself")
        return self


class ScheduleLineResponse(ORMModel):
    id: str = Field(validation_alias="public_id")
    day_of_week: Weekday
    start_time: time
    end_time: time
    break_minutes: int


class WorkingScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=180)
    schedule_type: WorkingScheduleType = WorkingScheduleType.FULL_TIME
    lines: List[ScheduleLineInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_overlapping_blocks_on_a_day(self) -> "WorkingScheduleCreate":
        _assert_no_overlaps(self.lines)
        return self


class WorkingScheduleUpdate(BaseModel):
    """`lines` omitted leaves the existing lines alone; `lines` present
    REPLACES them wholesale.

    Replacement rather than a per-line patch language because a weekly pattern
    is edited as a whole in the UI (add a row, change a time, delete a row,
    save), and a partial-update protocol for an ordered child collection is a
    lot of machinery for an interaction nobody performs.
    """

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=180)
    schedule_type: Optional[WorkingScheduleType] = None
    lines: Optional[List[ScheduleLineInput]] = None

    @model_validator(mode="after")
    def _no_overlapping_blocks_on_a_day(self) -> "WorkingScheduleUpdate":
        if self.lines is not None:
            _assert_no_overlaps(self.lines)
        return self


class WorkingScheduleResponse(ORMModel):
    id: str = Field(validation_alias="public_id")
    name: str
    schedule_type: WorkingScheduleType
    #: SERVER-COMPUTED from `lines`. Appears here and on no request schema.
    weekly_hours: Decimal
    lines: List[ScheduleLineResponse]
    version: int


def _assert_no_overlaps(lines: List[ScheduleLineInput]) -> None:
    """Two blocks on the same day may not overlap.

    Split shifts are the reason multiple lines per day are allowed at all, so
    this cannot simply forbid duplicate days. But overlapping blocks would
    double-count the overlap into weekly_hours, and weekly_hours feeds payroll
    — so an employee's schedule would silently claim hours nobody worked.
    """
    by_day: dict[Weekday, list[ScheduleLineInput]] = {}
    for line in lines:
        by_day.setdefault(line.day_of_week, []).append(line)

    for day, day_lines in by_day.items():
        ordered = sorted(day_lines, key=lambda line: line.start_time)
        for previous, current in zip(ordered, ordered[1:]):
            if current.start_time < previous.end_time:
                raise ValueError(
                    f"{day.value}: blocks {previous.start_time}-{previous.end_time} and "
                    f"{current.start_time}-{current.end_time} overlap"
                )
