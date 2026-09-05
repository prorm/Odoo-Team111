"""Attendance — one check-in/check-out record (PS B3).

`worked_hours` is server-computed from check_in/check_out, never client-
submitted, for the same reason WorkingSchedule.weekly_hours is: it is a payroll
input, and a payroll input a client can set is a payroll input a client can
falsify.

This is one of the two entities offline sync will register in Phase 8
(Architecture §8.3), and the scoping there is deliberate: check-in and
check-out may be queued offline, CORRECTIONS may not. A correction rewrites a
record that already exists and is restricted to authorized roles (PS B3), so
it stays online-only where the role check and the audit write happen
synchronously. `corrected_by` records who made one.
"""
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Numeric, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import AttendanceStatus
from app.models.mixins import AuditedEntity
from app.models.types import StrEnum

if TYPE_CHECKING:
    from app.models.employee import Employee
    from app.models.user import User


class Attendance(AuditedEntity, Base):
    __tablename__ = "attendances"

    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id"), nullable=False, index=True)

    check_in: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: NULL while someone is still checked in. A missing checkout on a day that
    #: has ended is one of the deterministic payroll warnings (PRD §5.7) and a
    #: blocking issue in the validation firewall (PRD §5.10) — which is why it
    #: is representable rather than defaulted to something plausible.
    check_out: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    #: SERVER-COMPUTED from check_in/check_out. Numeric, not Float
    #: (Architecture §10): worked hours multiply into money.
    worked_hours: Mapped[Optional[Decimal]] = mapped_column(Numeric(6, 2), nullable=True)

    status: Mapped[AttendanceStatus] = mapped_column(
        StrEnum(AttendanceStatus), default=AttendanceStatus.PRESENT, nullable=False, index=True
    )

    #: Set when an authorized role edits a record after the fact. Points at
    #: `users`, not `employees`: the person who corrects a record is acting in
    #: their capacity as an HR login, and may have no Employee row at all.
    corrected_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    correction_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    employee: Mapped["Employee"] = relationship(
        "Employee", back_populates="attendances", foreign_keys=[employee_id], lazy="selectin"
    )
    corrector: Mapped[Optional["User"]] = relationship("User", foreign_keys=[corrected_by])

    __table_args__ = (
        CheckConstraint("check_out IS NULL OR check_out >= check_in", name="ck_attendance_interval"),
        # Every read of this table is "this employee, over this period" — the
        # payroll compute path, the employee's own attendance view, and the
        # dashboard's attendance overview alike.
        Index("ix_attendances_employee_check_in", "employee_id", "check_in"),
    )
