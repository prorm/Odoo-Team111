"""Employee — the HR record, and the hub every other entity hangs off (PS A1/B2).

Deliberately separate from `User` (app/models/user.py). `User` is a login;
`Employee` is a person the organisation employs. Splitting them means an
employee can exist in HR before they have a login and keep existing after it is
revoked, and a payroll-only or admin login can exist with no Employee row at
all. `user_id` is the optional, unique bridge between the two, and it is what
puts the `employee_id` claim in an access token so an "own records only" rule
has something server-signed to compare against (Architecture §5, first row).
"""
from datetime import date
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import BigInteger, Date, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import EmployeeStatus, EmployeeType
from app.models.mixins import AuditedEntity
from app.models.types import StrEnum

if TYPE_CHECKING:
    from app.models.attendance import Attendance
    from app.models.contract import Contract
    from app.models.department import Department
    from app.models.time_off import TimeOffAllocation, TimeOffRequest
    from app.models.user import User
    from app.models.working_schedule import WorkingSchedule


class Employee(AuditedEntity, Base):
    __tablename__ = "employees"

    # --- identity -----------------------------------------------------------
    first_name: Mapped[str] = mapped_column(String(120), nullable=False)
    last_name: Mapped[str] = mapped_column(String(120), nullable=False)
    work_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    #: The login this employee owns, if any. Unique — one Employee per User, so
    #: the `employee_id` token claim can never be ambiguous.
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True, unique=True)

    # --- organisation -------------------------------------------------------
    department_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("departments.id"), nullable=True, index=True
    )
    #: Self-FK. Nullable and unconstrained beyond that: an org chart with no
    #: root would be unrepresentable, and enforcing acyclicity in the schema
    #: costs a recursive CHECK for a problem the UI prevents at entry.
    manager_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("employees.id"), nullable=True, index=True)
    job_position: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    employee_type: Mapped[EmployeeType] = mapped_column(
        StrEnum(EmployeeType), default=EmployeeType.PERMANENT, nullable=False, index=True
    )
    status: Mapped[EmployeeStatus] = mapped_column(
        StrEnum(EmployeeStatus), default=EmployeeStatus.ACTIVE, nullable=False, index=True
    )

    # --- working time -------------------------------------------------------
    #: The employee's default schedule. A Contract may override it for the
    #: period it covers (PS A3), so payroll reads the contract's schedule first
    #: and falls back to this.
    default_schedule_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("working_schedules.id"), nullable=True
    )

    # --- employment dates ---------------------------------------------------
    hire_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    exit_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    # --- payroll ------------------------------------------------------------
    #: A plain account/IBAN string, per PRD §8's assumption — enough to drive
    #: the "missing bank details" payroll warning, not a verified banking
    #: integration.
    bank_account: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # --- relationships ------------------------------------------------------
    user: Mapped[Optional["User"]] = relationship("User", foreign_keys=[user_id], lazy="selectin")
    department: Mapped[Optional["Department"]] = relationship(
        "Department", back_populates="employees", foreign_keys=[department_id], lazy="selectin"
    )
    manager: Mapped[Optional["Employee"]] = relationship(
        "Employee", remote_side="Employee.id", foreign_keys=[manager_id], lazy="selectin"
    )
    reports: Mapped[List["Employee"]] = relationship(
        "Employee", back_populates=None, foreign_keys=[manager_id], overlaps="manager", viewonly=True
    )
    default_schedule: Mapped[Optional["WorkingSchedule"]] = relationship(
        "WorkingSchedule", foreign_keys=[default_schedule_id], lazy="selectin"
    )
    contracts: Mapped[List["Contract"]] = relationship(
        "Contract", back_populates="employee", foreign_keys="Contract.employee_id"
    )
    attendances: Mapped[List["Attendance"]] = relationship(
        "Attendance", back_populates="employee", foreign_keys="Attendance.employee_id"
    )
    time_off_allocations: Mapped[List["TimeOffAllocation"]] = relationship(
        "TimeOffAllocation", back_populates="employee", foreign_keys="TimeOffAllocation.employee_id"
    )
    time_off_requests: Mapped[List["TimeOffRequest"]] = relationship(
        "TimeOffRequest", back_populates="employee", foreign_keys="TimeOffRequest.employee_id"
    )

    __table_args__ = (
        # Scoped to tenant_id rather than globally unique, matching every other
        # natural key in the schema (uq_department_code_tenant).
        UniqueConstraint("work_email", "tenant_id", name="uq_employee_work_email_tenant"),
        # The Employee list and Kanban both group and filter on
        # (department, status); a composite index serves both orderings of that
        # query without a second index.
        Index("ix_employees_department_status", "department_id", "status"),
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()
