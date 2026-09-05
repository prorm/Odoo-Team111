"""Salary configuration — Structure, Rule, and the ordered link between them
(PS A5 / A6).

A SalaryStructure is an ordered container of SalaryRules; a Payrun's structure
dictates which rules run and in what order. The ordering lives on the LINK
(`SalaryStructureRule.sequence`), not only on the rule, because the same rule
can legitimately sit at different positions in different structures — a
"Professional Tax" deduction might run after HRA in one structure and before it
in another, and a rule that references an earlier rule's result by code needs
the structure's ordering, not a global one.

`SalaryRule.sequence` is kept as the rule's own default position, used when a
rule is added to a structure without an explicit one.

Nothing in this file is ever written by the AI layer. The deterministic rule
engine is the sole authority for every figure that reaches a payslip
(Architecture §7/§10); AI may read and narrate these rules, never author or
execute them.
"""
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import SalaryRuleCategory, SalaryRuleComputation
from app.models.mixins import AuditedEntity
from app.models.types import StrEnum

if TYPE_CHECKING:
    pass


class SalaryRule(AuditedEntity, Base):
    """One computation step on a payslip (PS A6).

    `code` is the identifier later rules reference — a formula rule computing
    net pay reads `GROSS` and `TAX` by code, so codes are the rule engine's
    variable namespace and are unique per tenant for that reason.
    """

    __tablename__ = "salary_rules"

    name: Mapped[str] = mapped_column(String(180), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[SalaryRuleCategory] = mapped_column(StrEnum(SalaryRuleCategory), nullable=False, index=True)
    #: Default position; a structure may override it on the link row.
    sequence: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    computation_method: Mapped[SalaryRuleComputation] = mapped_column(
        StrEnum(SalaryRuleComputation), default=SalaryRuleComputation.FIXED, nullable=False
    )
    #: The amount for FIXED, or the percentage for PERCENTAGE. Numeric, never
    #: Float (Architecture §10).
    amount: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    #: For PERCENTAGE: which earlier rule's code this is a percentage OF.
    #: Without it, "10%" has no referent.
    percentage_base_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    #: For FORMULA: a `simpleeval` expression over named inputs
    #: (WORKED_DAYS, CONTRACT_WAGE, and earlier rules by code). A restricted
    #: grammar, never `eval()` — Architecture §1 and PRD §8.
    expression: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    structure_links: Mapped[List["SalaryStructureRule"]] = relationship(
        "SalaryStructureRule", back_populates="salary_rule", foreign_keys="SalaryStructureRule.salary_rule_id"
    )

    __table_args__ = (UniqueConstraint("code", "tenant_id", name="uq_salary_rule_code_tenant"),)


class SalaryStructure(AuditedEntity, Base):
    """An ordered set of rules a payrun executes (PS A5)."""

    __tablename__ = "salary_structures"

    name: Mapped[str] = mapped_column(String(180), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    rule_links: Mapped[List["SalaryStructureRule"]] = relationship(
        "SalaryStructureRule",
        back_populates="structure",
        cascade="all, delete-orphan",
        order_by="SalaryStructureRule.sequence",
        lazy="selectin",
    )

    __table_args__ = (UniqueConstraint("code", "tenant_id", name="uq_salary_structure_code_tenant"),)


class SalaryStructureRule(Base):
    """Ordered membership of a rule in a structure.

    A pure line-item child (Architecture §4): no AuditedEntity, because it has
    no lifecycle of its own. Reordering a structure's rules is an edit to the
    STRUCTURE, and it is the structure's `version` column that guards it — and
    the advisory lock, once Phase 3 adds bulk resequencing, since rewriting a
    whole ordered child collection is precisely the read-compute-write
    `app/core/locks.py` exists for.
    """

    __tablename__ = "salary_structure_rules"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    structure_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("salary_structures.id", ondelete="CASCADE"), nullable=False, index=True
    )
    salary_rule_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("salary_rules.id"), nullable=False, index=True)
    #: Execution order within THIS structure. Rules run in ascending sequence,
    #: and a rule may reference the result of any rule with a lower one.
    sequence: Mapped[int] = mapped_column(Integer, default=100, nullable=False)

    structure: Mapped["SalaryStructure"] = relationship("SalaryStructure", back_populates="rule_links")
    salary_rule: Mapped["SalaryRule"] = relationship(
        "SalaryRule", back_populates="structure_links", foreign_keys=[salary_rule_id], lazy="selectin"
    )

    __table_args__ = (
        # A rule appears at most once in a structure — twice would run it twice
        # and double whatever it adds or deducts.
        UniqueConstraint("structure_id", "salary_rule_id", name="uq_structure_rule_unique"),
    )
