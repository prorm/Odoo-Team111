"""Time Off — Type, Allocation and Request (PS A4 / B4).

The three tables answer three different questions:

    TimeOffType        what KINDS of leave exist, and how each behaves
                       (unit, whether it needs an allocation, whether it needs
                       approval, whether payroll cares about it)
    TimeOffAllocation  how much of a type an employee HAS, and until when
    TimeOffRequest     a specific absence, and where it is in approval

`remaining` is deliberately NOT a stored column. It is `allocated - taken`, and
storing it would create a third number that can disagree with the other two —
the classic denormalisation bug, on a value employees will dispute. `taken` IS
stored, because it is the running total that approving a request increments
inside the same transaction that approves it (PS A4: "approved requests
auto-deduct from allocations"), guarded by the allocation's `version` column so
two concurrent approvals cannot both read the same balance and both spend it.
"""
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import TimeOffAllocationStatus, TimeOffRequestStatus, TimeOffUnit
from app.models.mixins import AuditedEntity
from app.models.types import StrEnum

if TYPE_CHECKING:
    from app.models.employee import Employee
    from app.models.user import User


class TimeOffType(AuditedEntity, Base):
    """A kind of leave, and the policy attached to it (PS A4)."""

    __tablename__ = "time_off_types"

    name: Mapped[str] = mapped_column(String(180), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    unit: Mapped[TimeOffUnit] = mapped_column(StrEnum(TimeOffUnit), default=TimeOffUnit.DAYS, nullable=False)

    #: When false, an employee may request this type without holding an
    #: allocation for it (unpaid leave, for instance) and nothing is deducted.
    requires_allocation: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: When false, a request is approved on submission.
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: When true, approved absences of this type reach the payroll computation
    #: context (e.g. as UNPAID_LEAVE_DAYS). When false, payroll ignores them.
    payroll_integration: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    allocations: Mapped[List["TimeOffAllocation"]] = relationship(
        "TimeOffAllocation", back_populates="time_off_type", foreign_keys="TimeOffAllocation.time_off_type_id"
    )
    requests: Mapped[List["TimeOffRequest"]] = relationship(
        "TimeOffRequest", back_populates="time_off_type", foreign_keys="TimeOffRequest.time_off_type_id"
    )

    __table_args__ = (UniqueConstraint("code", "tenant_id", name="uq_time_off_type_code_tenant"),)


class TimeOffAllocation(AuditedEntity, Base):
    """How much leave of one type an employee holds, and until when (PS A4)."""

    __tablename__ = "time_off_allocations"

    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id"), nullable=False, index=True)
    time_off_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("time_off_types.id"), nullable=False, index=True
    )

    #: Numeric, not Integer: a half-day is 0.5 and an hours-unit type needs
    #: fractions. Not Float, for the usual reason — these accumulate.
    allocated: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0.00"), nullable=False)
    #: Incremented when a request is approved, inside the approving
    #: transaction. `version` (from AuditedEntity) is what stops two concurrent
    #: approvals from both reading the same balance and both spending it.
    taken: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0.00"), nullable=False)

    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
    valid_to: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    status: Mapped[TimeOffAllocationStatus] = mapped_column(
        StrEnum(TimeOffAllocationStatus), default=TimeOffAllocationStatus.DRAFT, nullable=False, index=True
    )

    employee: Mapped["Employee"] = relationship(
        "Employee", back_populates="time_off_allocations", foreign_keys=[employee_id], lazy="selectin"
    )
    time_off_type: Mapped["TimeOffType"] = relationship(
        "TimeOffType", back_populates="allocations", foreign_keys=[time_off_type_id], lazy="selectin"
    )

    __table_args__ = (
        Index("ix_time_off_allocations_employee_type", "employee_id", "time_off_type_id"),
        CheckConstraint("allocated >= 0 AND taken >= 0 AND taken <= allocated", name="ck_allocation_balance"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="ck_allocation_validity"),
    )

    @property
    def remaining(self) -> Decimal:
        """Derived, never stored — see the module docstring."""
        return (self.allocated or Decimal("0.00")) - (self.taken or Decimal("0.00"))


class TimeOffRequest(AuditedEntity, Base):
    """One requested absence, and where it sits in approval (PS B4)."""

    __tablename__ = "time_off_requests"

    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id"), nullable=False, index=True)
    time_off_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("time_off_types.id"), nullable=False, index=True
    )

    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)

    #: SERVER-COMPUTED from the date range and the type's unit, never
    #: client-submitted — it is the amount deducted from an allocation, so it
    #: is a number with a balance attached to it.
    duration: Mapped[Decimal] = mapped_column(Numeric(8, 2), default=Decimal("0.00"), nullable=False)

    status: Mapped[TimeOffRequestStatus] = mapped_column(
        StrEnum(TimeOffRequestStatus), default=TimeOffRequestStatus.DRAFT, nullable=False, index=True
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: Who approved or refused. Points at `users` — approving is an act of
    #: authority held by a login, and the approver need not be an Employee.
    approved_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    decision_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    #: The allocation this request drew down, recorded at approval time. Kept
    #: so a later cancellation credits back the same allocation rather than
    #: guessing which one applied.
    allocation_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("time_off_allocations.id"), nullable=True
    )

    employee: Mapped["Employee"] = relationship(
        "Employee", back_populates="time_off_requests", foreign_keys=[employee_id], lazy="selectin"
    )
    time_off_type: Mapped["TimeOffType"] = relationship(
        "TimeOffType", back_populates="requests", foreign_keys=[time_off_type_id], lazy="selectin"
    )
    approver: Mapped[Optional["User"]] = relationship("User", foreign_keys=[approved_by])

    __table_args__ = (
        # The approver's queue ("everything awaiting me") and the payroll
        # context's "approved leave in this period" both read this shape.
        Index("ix_time_off_requests_status_dates", "status", "date_from", "date_to"),
        CheckConstraint("date_to >= date_from AND duration >= 0", name="ck_request_dates_duration"),
    )
