"""WorkingSchedule + ScheduleLine — the weekly working pattern (PS A3).

The rule that shapes this file: **weekly hours are auto-computed, never
manually entered.** `weekly_hours` is therefore a stored column that only the
service layer writes, derived from the schedule's lines. It is stored rather
than computed on read so payroll can resolve a period's hours with one join
instead of re-summing lines for every employee in a payrun, and so a schedule
edited after a payrun cannot retroactively change what that payrun computed.

`ScheduleLine` is a pure line-item child: no `AuditedEntity`, so no version or
audit columns. Architecture §4 draws that line, and it is the right one — a
line has no independent lifecycle. Editing a schedule's lines is an edit to the
schedule, and it is the SCHEDULE's version column that guards it.
"""
from datetime import time
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import BigInteger, ForeignKey, Integer, Numeric, String, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import Weekday, WorkingScheduleType
from app.models.mixins import AuditedEntity
from app.models.types import StrEnum

if TYPE_CHECKING:
    pass


class WorkingSchedule(AuditedEntity, Base):
    __tablename__ = "working_schedules"

    name: Mapped[str] = mapped_column(String(180), nullable=False)
    schedule_type: Mapped[WorkingScheduleType] = mapped_column(
        StrEnum(WorkingScheduleType), default=WorkingScheduleType.FULL_TIME, nullable=False
    )

    #: SERVER-COMPUTED ONLY (PS A3). Never accepted from a request body; the
    #: create/update schemas do not expose it. Numeric, not Float: it feeds
    #: per-hour payroll arithmetic, and Rule 9 forbids Float anywhere on that
    #: path (Architecture §10).
    weekly_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"), nullable=False)

    lines: Mapped[List["ScheduleLine"]] = relationship(
        "ScheduleLine",
        back_populates="schedule",
        cascade="all, delete-orphan",
        order_by="ScheduleLine.id",
        lazy="selectin",
    )


class ScheduleLine(Base):
    """One working block on one weekday: Day / Start / End / Break.

    Multiple lines per day are allowed on purpose — a split shift (09:00-13:00,
    14:00-18:00) is two lines, and forcing it into one row with an implicit gap
    would make the break unrepresentable. The uniqueness constraint is
    therefore on (schedule, day, start_time), not on (schedule, day).
    """

    __tablename__ = "schedule_lines"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    schedule_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("working_schedules.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day_of_week: Mapped[Weekday] = mapped_column(StrEnum(Weekday), nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    #: Unpaid break inside this block, in minutes. Minutes rather than a
    #: fractional-hours Numeric because that is how people enter it ("45 min"),
    #: and an integer cannot accumulate a rounding error across a week.
    break_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    schedule: Mapped["WorkingSchedule"] = relationship("WorkingSchedule", back_populates="lines")

    __table_args__ = (
        UniqueConstraint("schedule_id", "day_of_week", "start_time", name="uq_schedule_line_day_start"),
    )
