"""Payroll — Payrun, its selected employees, and the resulting Payslips
(PS B5 / B6 / B7).

Shape of a run:

    Payrun            one execution of one SalaryStructure over one period
    PayrunEmployee    the explicitly selected employees (PS B5 step 2 —
                      selection is explicit, never "everyone active")
    Payslip           one employee's result for that run, tied to the ONE
                      contract applicable to the period
    PayslipLine       one Salary Rule's contribution to that payslip

Two properties this schema exists to protect:

  * **Historical fidelity.** `Payslip.contract_id` is stored, not resolved on
    read. A payslip computed in March must keep showing the contract that was
    applicable in March, even after that contract is superseded — Architecture
    §5.8's "time machine" depends on it, and so does anyone auditing a payment.

  * **Money is exact.** Every amount is `Numeric(12,2)` and every layer handles
    it as `Decimal`, stringified across any JSON boundary (Taskiq payload, MCP
    tool response, AI prompt context). No `Float` appears anywhere on this
    path — Architecture §10.

AI has no write path to either table. That is architectural, not a convention:
the rule engine is the only thing that constructs a Payslip or a PayslipLine,
and the AI layer calls read services only (Architecture §7/§10).
"""
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any, List, Optional

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import PayrunStatus, PayslipStatus, SalaryRuleCategory
from app.models.mixins import AuditedEntity
from app.models.types import StrEnum

if TYPE_CHECKING:
    from app.models.contract import Contract
    from app.models.employee import Employee
    from app.models.salary import SalaryRule, SalaryStructure


class Payrun(AuditedEntity, Base):
    """One payroll execution over one period (PS B5/B6).

    `Idempotency-Key` is required on create and compute (Architecture §6), and
    compute is wrapped in `acquire_entity_lock(session, "payrun", id)` because
    it deletes and rewrites this run's entire payslip set — two concurrent
    computes would otherwise interleave into a set neither intended.
    """

    __tablename__ = "payruns"
    #: Blocking context failures for selected employees whose LOP-dependent
    #: structure cannot produce a payslip. Replaced on every compute.
    computation_warnings: Mapped[Optional[list[Any]]] = mapped_column(JSONB, nullable=True)

    name: Mapped[str] = mapped_column(String(180), nullable=False)
    salary_structure_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("salary_structures.id"), nullable=False, index=True
    )
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[PayrunStatus] = mapped_column(
        StrEnum(PayrunStatus), default=PayrunStatus.DRAFT, nullable=False, index=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    salary_structure: Mapped["SalaryStructure"] = relationship(
        "SalaryStructure", foreign_keys=[salary_structure_id], lazy="selectin"
    )
    selected_employees: Mapped[List["PayrunEmployee"]] = relationship(
        "PayrunEmployee", back_populates="payrun", cascade="all, delete-orphan", lazy="selectin"
    )
    payslips: Mapped[List["Payslip"]] = relationship(
        "Payslip", back_populates="payrun", foreign_keys="Payslip.payrun_id"
    )

    __table_args__ = (Index("ix_payruns_period", "period_start", "period_end"),)


class PayrunEmployee(Base):
    """An employee explicitly selected into a payrun (PS B5, step 2).

    A pure line-item child: no AuditedEntity. Selection is a property of the
    payrun, and the payrun's `version` guards edits to it.
    """

    __tablename__ = "payrun_employees"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    payrun_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("payruns.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id"), nullable=False, index=True)

    payrun: Mapped["Payrun"] = relationship("Payrun", back_populates="selected_employees")
    employee: Mapped["Employee"] = relationship("Employee", foreign_keys=[employee_id], lazy="selectin")

    __table_args__ = (
        UniqueConstraint("payrun_id", "employee_id", name="uq_payrun_employee_unique"),
    )


class Payslip(AuditedEntity, Base):
    """One employee's result for one payrun (PS B7)."""

    __tablename__ = "payslips"
    #: Historical public references and Decimal-string context, captured once
    #: by compute. Nullable only for legacy rows without recoverable snapshots.
    reference_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    context_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    payrun_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("payruns.id"), nullable=False, index=True)
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id"), nullable=False, index=True)
    #: The ONE contract applicable to this payrun's period, resolved at compute
    #: time and then frozen. Uniqueness of that resolution is guaranteed by the
    #: Contract non-overlap EXCLUDE constraint (Architecture §6), which is why
    #: this can be a single FK rather than a list.
    contract_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("contracts.id"), nullable=False)

    worked_days: Mapped[Decimal] = mapped_column(Numeric(6, 2), default=Decimal("0.00"), nullable=False)

    #: Denormalised totals, so a payslip list or the dashboard's "salary cost by
    #: department" does not have to re-aggregate every line. Written only by the
    #: rule engine, in the same transaction as the lines they summarise.
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)

    status: Mapped[PayslipStatus] = mapped_column(
        StrEnum(PayslipStatus), default=PayslipStatus.DRAFT, nullable=False, index=True
    )

    #: Deterministic warnings from the §7 step 6 checks — missing bank details,
    #: missing checkout, contract gap, duplicate payslip attempt. JSONB rather
    #: than JSON so the validation firewall (PRD §5.10) can aggregate and filter
    #: across a whole payrun in the database instead of in Python.
    warnings: Mapped[Optional[list[Any]]] = mapped_column(JSONB, nullable=True)

    payrun: Mapped["Payrun"] = relationship("Payrun", back_populates="payslips", foreign_keys=[payrun_id])
    employee: Mapped["Employee"] = relationship("Employee", foreign_keys=[employee_id], lazy="selectin")
    contract: Mapped["Contract"] = relationship("Contract", foreign_keys=[contract_id], lazy="selectin")
    lines: Mapped[List["PayslipLine"]] = relationship(
        "PayslipLine",
        back_populates="payslip",
        cascade="all, delete-orphan",
        order_by="PayslipLine.sequence",
        lazy="selectin",
    )

    __table_args__ = (
        # One payslip per employee per payrun. This is the database half of the
        # "duplicate payslip attempt is caught, not silently duplicated"
        # acceptance criterion; the Idempotency-Key middleware is the other
        # half, catching the double-click before it reaches here.
        UniqueConstraint("payrun_id", "employee_id", name="uq_payslip_payrun_employee"),
    )


class PayslipLine(Base):
    """One Salary Rule's contribution to one payslip (PS B7).

    A pure line-item child: no AuditedEntity. Lines are never edited — a payrun
    is recomputed, which replaces them wholesale — so per-line versioning would
    guard nothing.

    `code`, `name` and `category` are COPIED from the rule rather than only
    referenced through `salary_rule_id`. That is deliberate denormalisation: a
    payslip must keep rendering the rule as it was when it ran, even if the
    rule is later renamed, recategorised or deleted. `salary_rule_id` stays
    nullable for exactly that last case.
    """

    __tablename__ = "payslip_lines"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    payslip_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("payslips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    salary_rule_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("salary_rules.id"), nullable=True)

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    category: Mapped[SalaryRuleCategory] = mapped_column(StrEnum(SalaryRuleCategory), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"), nullable=False)

    payslip: Mapped["Payslip"] = relationship("Payslip", back_populates="lines")
    salary_rule: Mapped[Optional["SalaryRule"]] = relationship("SalaryRule", foreign_keys=[salary_rule_id])

    __table_args__ = (
        UniqueConstraint("payslip_id", "code", name="uq_payslip_line_code"),
    )
