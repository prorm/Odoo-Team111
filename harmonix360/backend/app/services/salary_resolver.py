"""The structure resolver — Architecture §7's exact "execute each SalaryRule
in sequence" step, factored out as a standalone, DB-free, AI-free function.

`resolve_salary_structure` is THE function Phase 4's payroll engine calls: it
takes an ordered structure and a period's inputs, and returns exactly what a
payslip's line items are built from. It touches no session, no repository, no
HTTP — a hand-built `SalaryStructure` and a plain `dict` are enough to test it,
which is why the acceptance test for this phase hand-computes a payslip on
paper and asserts this function reproduces it exactly.

Nothing here is AI-assisted (PRD §8, Architecture §7/§10): FIXED and PERCENTAGE
are plain Decimal arithmetic, and FORMULA goes through `simpleeval` with an
empty function table and a names dict limited to exactly what has already run
— never Python's `eval()`, never an open namespace.

SAVE-TIME vs RUN-TIME validation
---------------------------------
`validate_structure_rule_order` runs when a SalaryStructure is created or
updated (see app/services/salary.py), not when a payrun calls the resolver. A
formula referencing a rule that hasn't executed yet in this structure's order,
or a name nobody defined, is an AUTHORING mistake — the structure should never
have been saved in that shape, and refusing it at save time means Phase 4
never has to handle that failure mode at all: by the time a payrun runs, every
saved structure is already known-resolvable. `resolve_salary_structure` still
defends itself at evaluation time (simpleeval's restricted grammar, no
functions), because a formula's *safety* against injection is not the same
question as a structure's *shape* being well-ordered — both are checked, at
the point each one can actually be known.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import TYPE_CHECKING, Mapping, Sequence

import simpleeval

from app.models.enums import SalaryRuleCategory, SalaryRuleComputation

if TYPE_CHECKING:
    from app.models.salary import SalaryRule, SalaryStructure

TWO_PLACES = Decimal("0.01")
HUNDRED = Decimal("100")

#: Names available to every formula/percentage-base BEFORE any rule in the
#: structure has run (Architecture §7's context: WORKED_DAYS, CONTRACT_WAGE,
#: UNPAID_LEAVE_DAYS, "etc."). This is a fixed, documented vocabulary rather
#: than whatever Phase 4 happens to pass at a given payrun — that is what
#: makes it possible to validate a structure's formulas at SAVE time, before
#: any payrun context exists at all. A name that is genuinely new payroll
#: input (not yet in this set) is a Phase 4 change to this constant, made
#: deliberately, not something a structure author can introduce by typing it
#: into a formula.
SEED_CONTEXT_NAMES = frozenset({"WORKED_DAYS", "CONTRACT_WAGE", "UNPAID_LEAVE_DAYS", "LOP_AMOUNT"})

#: The only AST node types a formula may contain: a bare arithmetic
#: expression over names and numbers. No calls, no attribute access, no
#: subscripts, no comprehensions, no assignments — there is no legitimate
#: payroll formula that needs any of those, and every one of them is also a
#: known injection vector. This is checked BEFORE the expression ever reaches
#: simpleeval, so a `__import__(...)`-shaped formula is refused for having an
#: `ast.Call` node at all, not merely because `__import__` is an undefined
#: name or function.
_ALLOWED_EXPRESSION_NODES = (
    ast.Expression,
    ast.BinOp,
    ast.UnaryOp,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.FloorDiv,
    ast.Mod,
    ast.Pow,
    ast.USub,
    ast.UAdd,
)


@dataclass(frozen=True)
class ResolvedRule:
    """One resolved line of a structure — becomes one PayslipLine in Phase 4."""

    code: str
    category: SalaryRuleCategory
    amount: Decimal


class StructureValidationError(ValueError):
    """A structure's rule ordering or a rule's formula cannot be resolved
    safely. Raised at structure-save time; the router turns it into a 400."""

    def __init__(self, rule_code: str, reason: str):
        self.rule_code = rule_code
        self.reason = reason
        super().__init__(f"salary rule '{rule_code}': {reason}")


class FormulaEvaluationError(RuntimeError):
    """A formula failed to evaluate against a specific run's context (divide
    by zero, an unexpected result type). Distinct from
    `StructureValidationError`: that one is an authoring mistake refused
    before save; this one is a runtime data problem in an otherwise
    well-formed, already-saved structure."""

    def __init__(self, rule_code: str, reason: str):
        self.rule_code = rule_code
        self.reason = reason
        super().__init__(f"salary rule '{rule_code}': {reason}")


class _DecimalSafeEval(simpleeval.SimpleEval):
    """`simpleeval.SimpleEval` whose numeric literals come back as `Decimal`.

    Without this, a formula that mixes a literal like `0.5` with a named
    rule code (already a Decimal, because every monetary value in this system
    is Decimal end to end — Architecture §10) raises a bare `TypeError` at
    evaluation time: `Decimal.__mul__` refuses a `float` operand. Coercing
    float constants to `Decimal(str(value))` here means a formula's numeric
    literals behave the way every other number in the payroll path already
    does, without touching simpleeval's operator/function restrictions at all.
    """

    def _eval_constant(self, node):
        value = super()._eval_constant(node)
        if isinstance(value, float):
            return Decimal(str(value))
        return value

    # Python's `ast` module deprecated (and, on newer versions, removed)
    # `ast.Num` in favour of `ast.Constant`; simpleeval only registers this
    # override when the node type still exists (see its own `__init__`), so
    # mirroring `_eval_constant` here is a no-op everywhere it isn't needed.
    _eval_num = _eval_constant


def _referenced_names(expression: str) -> set[str]:
    """Parse a formula and return the rule/seed names it references.

    Raises `ValueError` for anything that is not valid arithmetic syntax over
    names and numbers — a syntax error, or a disallowed construct such as a
    call, attribute access, or subscript (see `_ALLOWED_EXPRESSION_NODES`).
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"'{expression}' is not a valid expression: {exc.msg}") from exc

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif not isinstance(node, _ALLOWED_EXPRESSION_NODES):
            raise ValueError(
                f"'{expression}' uses unsupported syntax ({type(node).__name__}); "
                "only +, -, *, /, %, ** over named rule codes and numbers is allowed"
            )
    return names


def validate_formula_syntax(expression: str) -> set[str]:
    """Rule-save-time shape check: is this a well-formed arithmetic
    expression at all? Returns the names it references (unvalidated against
    any ordering — that is `validate_structure_rule_order`'s job, since
    ordering is a property of a STRUCTURE, not of a standalone rule: the same
    rule can sit at different positions in different structures).
    """
    return _referenced_names(expression)


def validate_structure_rule_order(ordered_rules: Sequence["SalaryRule"]) -> None:
    """Save-time check for a structure's rules, in their execution order.

    Every PERCENTAGE rule's `percentage_base_code` and every FORMULA rule's
    referenced names must resolve to either a seed input
    (`SEED_CONTEXT_NAMES`) or the `code` of a rule earlier in THIS ordering.
    A rule that hasn't run yet, a typo, or an injection attempt like
    `__import__` are all, structurally, the same failure: a reference to a
    name nothing in this structure's history defines — and are all refused
    here, before the structure is ever saved.

    A rule may not reference its own code either: `known` only gains this
    rule's code AFTER it has been validated, so a self-reference is
    indistinguishable from — and rejected the same way as — forward
    reference.
    """
    known: set[str] = set(SEED_CONTEXT_NAMES)

    for rule in ordered_rules:
        if rule.computation_method == SalaryRuleComputation.PERCENTAGE:
            base_code = rule.percentage_base_code
            if not base_code:
                raise StructureValidationError(rule.code, "percentage rule has no percentage_base_code")
            if base_code not in known:
                raise StructureValidationError(
                    rule.code, f"references '{base_code}', which has not run yet (or is unknown)"
                )
        elif rule.computation_method == SalaryRuleComputation.FORMULA:
            if not rule.expression:
                raise StructureValidationError(rule.code, "formula rule has no expression")
            try:
                referenced = _referenced_names(rule.expression)
            except ValueError as exc:
                raise StructureValidationError(rule.code, str(exc)) from exc
            unknown = referenced - known
            if unknown:
                raise StructureValidationError(
                    rule.code,
                    f"references {sorted(unknown)}, which have not run yet (or are unknown)",
                )
        elif rule.computation_method == SalaryRuleComputation.FIXED:
            if rule.amount is None:
                raise StructureValidationError(rule.code, "fixed rule has no amount")

        known.add(rule.code)


def _compute_rule_amount(rule: "SalaryRule", context: Mapping[str, Decimal]) -> Decimal:
    if rule.computation_method == SalaryRuleComputation.FIXED:
        if rule.amount is None:
            raise FormulaEvaluationError(rule.code, "fixed rule has no amount")
        return rule.amount.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    if rule.computation_method == SalaryRuleComputation.PERCENTAGE:
        base_code = rule.percentage_base_code
        if rule.amount is None or not base_code:
            raise FormulaEvaluationError(rule.code, "percentage rule missing amount or percentage_base_code")
        if base_code not in context:
            raise FormulaEvaluationError(rule.code, f"'{base_code}' has not run yet")
        base_value = context[base_code]
        return (base_value * rule.amount / HUNDRED).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    if rule.computation_method == SalaryRuleComputation.FORMULA:
        if not rule.expression:
            raise FormulaEvaluationError(rule.code, "formula rule has no expression")
        evaluator = _DecimalSafeEval(functions={}, names=dict(context))
        try:
            result = evaluator.eval(rule.expression)
        except simpleeval.InvalidExpression as exc:
            # Covers NameNotDefined, FunctionNotDefined, and every other
            # simpleeval-raised grammar violation — the restricted-grammar
            # defense described in the module docstring.
            raise FormulaEvaluationError(rule.code, f"formula rejected: {exc}") from exc
        except (TypeError, ArithmeticError) as exc:
            raise FormulaEvaluationError(rule.code, f"formula evaluation failed: {exc}") from exc
        if not isinstance(result, Decimal):
            raise FormulaEvaluationError(
                rule.code, f"formula must evaluate to a number, got {type(result).__name__}"
            )
        return result.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    raise FormulaEvaluationError(rule.code, f"unknown computation_method {rule.computation_method!r}")


def resolve_ordered_rules(
    ordered_rules: Sequence["SalaryRule"], seed_context: Mapping[str, Decimal]
) -> list[ResolvedRule]:
    """The pure computation, over a plain ordered list of rules — no ORM
    object required, so a hand-built list of `SalaryRule(...)` instances (no
    session, no flush) is enough to test every computation method and the
    hand-computed acceptance payslip.

    Each rule's computed amount is stored into `context` under its own code
    immediately after computing it, so every later rule in the list can
    reference it — this is the "later rules can reference this one by code"
    half of Architecture §7 step 4, and is exactly why the ordering validated
    by `validate_structure_rule_order` matters: this function trusts that
    ordering completely and does not re-check it.
    """
    context: dict[str, Decimal] = dict(seed_context)
    resolved: list[ResolvedRule] = []

    for rule in ordered_rules:
        amount = _compute_rule_amount(rule, context)
        context[rule.code] = amount
        resolved.append(ResolvedRule(code=rule.code, category=rule.category, amount=amount))

    return resolved


def resolve_salary_structure(
    structure: "SalaryStructure", seed_context: Mapping[str, Decimal]
) -> list[ResolvedRule]:
    """THE function Phase 4's payroll engine calls (Architecture §7 step 4).

    Signature: `resolve_salary_structure(structure: SalaryStructure,
    seed_context: Mapping[str, Decimal]) -> list[ResolvedRule]`, where
    `ResolvedRule` is `(code: str, category: SalaryRuleCategory,
    amount: Decimal)`.

    `structure.rule_links` must already be loaded (it is: the relationship on
    `SalaryStructure` is `lazy="selectin"` and `order_by="SalaryStructureRule.
    sequence"` — see app/models/salary.py). An inactive rule (`is_active =
    False`) is skipped entirely, as if it were not in the structure at all;
    a rule can be authored and linked ahead of being turned on without ever
    contributing to a payslip in the meantime.
    """
    ordered_rules = [link.salary_rule for link in structure.rule_links if link.salary_rule.is_active]
    return resolve_ordered_rules(ordered_rules, seed_context)
