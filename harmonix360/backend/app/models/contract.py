"""Contract — the employment terms payroll actually reads (PS A2).

The single most important invariant in this product lives on this table:

    an employee may hold any number of contracts over time, but payroll must
    resolve EXACTLY ONE for the period being run.

That is enforced by a Postgres EXCLUDE constraint, not by application code
(Architecture §6, PRD §9's risk register):

    EXCLUDE USING gist (
      employee_id WITH =,
      daterange(start_date, end_date, '[]') WITH &&
    ) WHERE (status = 'active')

Three details in that predicate carry real weight:

  * `WHERE (status = 'active')` — only active rows participate. Historical,
    draft and cancelled contracts may overlap freely; they are history and
    proposals, not conflicts. This also means a status-only UPDATE that moves a
    row INTO 'active' can raise SQLSTATE 23P01, which is exactly the trap
    BaseService's docstring warns about: the guard is owed on every mutation
    path, not just on create.

  * `'[]'` — the range is inclusive at both ends. A contract ending on the 31st
    and another starting on the 31st DO overlap, because both are in force that
    day and payroll would have two candidates for it.

  * an open-ended contract has `end_date IS NULL`, which `daterange` treats as
    unbounded — so it correctly conflicts with everything after its start.
"""
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import ContractStatus
from app.models.mixins import AuditedEntity
from app.models.types import StrEnum

if TYPE_CHECKING:
    from app.models.department import Department
    from app.models.employee import Employee
    from app.models.salary import SalaryStructure
    from app.models.working_schedule import WorkingSchedule


class Contract(AuditedEntity, Base):
    __tablename__ = "contracts"

    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id"), nullable=False, index=True)
    department_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("departments.id"), nullable=True)
    job_position: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)

    #: Numeric(12,2), never Float — Architecture §10. A binary float cannot
    #: represent 0.10 exactly, so wages summed across a department drift; every
    #: monetary value in this system is Decimal end to end, stringified across
    #: any JSON boundary.
    wage: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    #: Which set of Salary Rules payroll runs for this contract. Nullable and
    #: unconstrained at the FK level until the SalaryStructure table is
    #: populated in Phase 3 — the column and its FK exist now so the schema is
    #: complete and Phase 3 adds no migration to Contract.
    salary_structure_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("salary_structures.id"), nullable=True
    )

    #: Optional per-contract override of the employee's default schedule
    #: (PS A3). Payroll reads this first and falls back to
    #: Employee.default_schedule_id.
    working_schedule_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("working_schedules.id"), nullable=True
    )

    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: NULL means open-ended, which `daterange` reads as unbounded — see the
    #: module docstring for what that means for the overlap constraint.
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    status: Mapped[ContractStatus] = mapped_column(
        StrEnum(ContractStatus), default=ContractStatus.DRAFT, nullable=False, index=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    employee: Mapped["Employee"] = relationship(
        "Employee", back_populates="contracts", foreign_keys=[employee_id], lazy="selectin"
    )
    department: Mapped[Optional["Department"]] = relationship(
        "Department", foreign_keys=[department_id], lazy="selectin"
    )
    salary_structure: Mapped[Optional["SalaryStructure"]] = relationship(
        "SalaryStructure", foreign_keys=[salary_structure_id], lazy="selectin"
    )
    working_schedule: Mapped[Optional["WorkingSchedule"]] = relationship(
        "WorkingSchedule", foreign_keys=[working_schedule_id], lazy="selectin"
    )

    __table_args__ = (
        # Serves both "this employee's contract history" and the
        # active-contract-for-a-period resolution payroll performs per employee.
        Index("ix_contracts_employee_status_dates", "employee_id", "status", "start_date"),
        # NOTE: the non-overlap EXCLUDE constraint
        # (`contracts_active_period_overlap_excl`) is NOT declared here. It
        # needs `daterange(...) WITH &&` over the btree_gist operator class,
        # which has no SQLAlchemy Core spelling that survives autogenerate
        # cleanly; it is created as raw SQL in the migration, exactly as the
        # platform foundation did for its own range-exclusion constraint.
        # See alembic/versions/016_peoplepay360_hr_domain.py.
    )
