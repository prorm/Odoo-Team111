"""Salary Structure / Salary Rule request and response shapes (PS A5/A6).

Two things this file enforces at the type level, on top of what the resolver
(`app/services/salary_resolver.py`) enforces at save time:

  * `code` must be a valid Python identifier. It is the rule engine's
    variable namespace — a formula references it as a bare name
    (`ast.Name`), and it becomes a `dict` key in the resolver's context — so
    anything that isn't a legal identifier could be typed into a formula but
    could never actually be referenced there.
  * A rule's computation-method-specific fields (`amount`,
    `percentage_base_code`, `expression`) are validated as a set: exactly the
    fields that method needs, and none of the fields the others need — a
    FIXED rule with a stray `expression` is not "extra data that gets
    ignored", it's a rule that looks like a formula rule to the next person
    who reads it.
"""
import re
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import SalaryRuleCategory, SalaryRuleComputation
from app.schemas.common import ORMModel
from app.services.salary_resolver import validate_formula_syntax

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_code(code: str) -> str:
    if not _IDENTIFIER_RE.match(code):
        raise ValueError(
            f"'{code}' is not a valid rule code — codes must look like a Python "
            "identifier (letters, digits, underscore, not starting with a digit) "
            "because a formula references them as bare names"
        )
    return code


class SalaryRuleBase(BaseModel):
    name: str = Field(min_length=1, max_length=180)
    code: str = Field(min_length=1, max_length=64)
    category: SalaryRuleCategory
    sequence: int = Field(default=100, ge=0)
    computation_method: SalaryRuleComputation = SalaryRuleComputation.FIXED

    #: FIXED: the amount. PERCENTAGE: the percentage (e.g. 40.00 for 40%,
    #: not 0.40 — Decimal end to end, never a fraction that looks like a typo
    #: waiting to happen). Unused, and must be omitted, for FORMULA.
    amount: Optional[Decimal] = Field(default=None, max_digits=12, decimal_places=2)
    #: PERCENTAGE only: which earlier rule's code this is a percentage OF.
    percentage_base_code: Optional[str] = Field(default=None, max_length=64)
    #: FORMULA only: a simpleeval expression over named inputs.
    expression: Optional[str] = None

    is_active: bool = True
    description: Optional[str] = None

    @model_validator(mode="after")
    def _validate_code_shape(self) -> "SalaryRuleBase":
        _validate_code(self.code)
        if self.percentage_base_code is not None:
            _validate_code(self.percentage_base_code)
        return self

    @model_validator(mode="after")
    def _validate_computation_fields(self) -> "SalaryRuleBase":
        method = self.computation_method
        if method == SalaryRuleComputation.FIXED:
            if self.amount is None:
                raise ValueError("a fixed rule requires 'amount'")
            if self.percentage_base_code is not None or self.expression is not None:
                raise ValueError("a fixed rule must not set 'percentage_base_code' or 'expression'")
        elif method == SalaryRuleComputation.PERCENTAGE:
            if self.amount is None or self.percentage_base_code is None:
                raise ValueError("a percentage rule requires both 'amount' and 'percentage_base_code'")
            if self.expression is not None:
                raise ValueError("a percentage rule must not set 'expression'")
        elif method == SalaryRuleComputation.FORMULA:
            if not self.expression:
                raise ValueError("a formula rule requires 'expression'")
            if self.amount is not None or self.percentage_base_code is not None:
                raise ValueError("a formula rule must not set 'amount' or 'percentage_base_code'")
            # Rule-save-time SHAPE check only (valid arithmetic syntax, no
            # calls/attributes/subscripts). Whether the names it references
            # have actually run yet is a property of a STRUCTURE's ordering,
            # not of this rule in isolation — see
            # salary_resolver.validate_structure_rule_order, run when a
            # structure that links this rule is saved.
            try:
                validate_formula_syntax(self.expression)
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
        return self


class SalaryRuleCreate(SalaryRuleBase):
    model_config = ConfigDict(extra="forbid")


class SalaryRuleUpdate(BaseModel):
    """Full editable-form replacement with required `version`, matching this
    codebase's other PATCH schemas (see progress.md) — not a sparse patch."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=180)
    code: str = Field(min_length=1, max_length=64)
    category: SalaryRuleCategory
    sequence: int = Field(default=100, ge=0)
    computation_method: SalaryRuleComputation
    amount: Optional[Decimal] = Field(default=None, max_digits=12, decimal_places=2)
    percentage_base_code: Optional[str] = Field(default=None, max_length=64)
    expression: Optional[str] = None
    is_active: bool = True
    description: Optional[str] = None
    version: int

    @model_validator(mode="after")
    def _validate_code_shape(self) -> "SalaryRuleUpdate":
        _validate_code(self.code)
        if self.percentage_base_code is not None:
            _validate_code(self.percentage_base_code)
        return self

    @model_validator(mode="after")
    def _validate_computation_fields(self) -> "SalaryRuleUpdate":
        method = self.computation_method
        if method == SalaryRuleComputation.FIXED:
            if self.amount is None:
                raise ValueError("a fixed rule requires 'amount'")
            if self.percentage_base_code is not None or self.expression is not None:
                raise ValueError("a fixed rule must not set 'percentage_base_code' or 'expression'")
        elif method == SalaryRuleComputation.PERCENTAGE:
            if self.amount is None or self.percentage_base_code is None:
                raise ValueError("a percentage rule requires both 'amount' and 'percentage_base_code'")
            if self.expression is not None:
                raise ValueError("a percentage rule must not set 'expression'")
        elif method == SalaryRuleComputation.FORMULA:
            if not self.expression:
                raise ValueError("a formula rule requires 'expression'")
            if self.amount is not None or self.percentage_base_code is not None:
                raise ValueError("a formula rule must not set 'amount' or 'percentage_base_code'")
            validate_formula_syntax(self.expression)
        return self


class SalaryRuleResponse(ORMModel):
    id: str = Field(validation_alias="public_id")
    name: str
    code: str
    category: SalaryRuleCategory
    sequence: int
    computation_method: SalaryRuleComputation
    amount: Optional[Decimal] = None
    percentage_base_code: Optional[str] = None
    expression: Optional[str] = None
    is_active: bool
    description: Optional[str] = None
    version: int


class SalaryRuleRef(ORMModel):
    """A rule as it appears nested inside a structure's rule list."""

    id: str = Field(validation_alias="public_id")
    name: str
    code: str
    category: SalaryRuleCategory
    computation_method: SalaryRuleComputation
    is_active: bool


class SalaryStructureRuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    salary_rule_id: str
    #: Execution order WITHIN this structure — see app/models/salary.py for
    #: why this lives on the link rather than only on the rule.
    sequence: int = Field(default=100, ge=0)


class SalaryStructureRuleLinkResponse(ORMModel):
    id: str = Field(validation_alias="public_id")
    sequence: int
    salary_rule: SalaryRuleRef


class SalaryStructureCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=180)
    code: str = Field(min_length=1, max_length=64)
    is_active: bool = True
    description: Optional[str] = None
    rules: List[SalaryStructureRuleInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_duplicate_rules(self) -> "SalaryStructureCreate":
        ids = [r.salary_rule_id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("a rule may appear at most once in a structure")
        return self


class SalaryStructureUpdate(BaseModel):
    """`rules` omitted leaves the existing rule set alone; `rules` present
    REPLACES it wholesale — same replace-the-whole-ordered-collection
    convention as WorkingScheduleUpdate.lines."""

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=180)
    code: Optional[str] = Field(default=None, min_length=1, max_length=64)
    is_active: Optional[bool] = None
    description: Optional[str] = None
    rules: Optional[List[SalaryStructureRuleInput]] = None

    @model_validator(mode="after")
    def _no_duplicate_rules(self) -> "SalaryStructureUpdate":
        if self.rules is not None:
            ids = [r.salary_rule_id for r in self.rules]
            if len(ids) != len(set(ids)):
                raise ValueError("a rule may appear at most once in a structure")
        return self


class SalaryStructureResponse(ORMModel):
    id: str = Field(validation_alias="public_id")
    name: str
    code: str
    is_active: bool
    description: Optional[str] = None
    rules: List[SalaryStructureRuleLinkResponse] = Field(validation_alias="rule_links")
    #: SERVER-COMPUTED, set by the router AFTER `model_validate` (same
    #: two-step pattern as `ContractResponse.is_currently_active`) — neither
    #: is a column on the ORM row, so each defaults here purely to let
    #: `model_validate` succeed before the router fills in the real value.
    rule_count: int = 0
    contract_usage_count: int = 0
    version: int
