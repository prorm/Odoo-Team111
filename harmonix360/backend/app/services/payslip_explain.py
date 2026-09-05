"""Read-only payslip calculation tree (PRD §5.6, Architecture §8.6).

    inputs (frozen at compute)
        -> lines in the sequence the rules actually ran
            -> categorised totals
                -> the payslip's own persisted gross/net

Everything below is rendered from `Payslip`, `PayslipLine` and
`Payslip.context_snapshot`. No rule is evaluated, no formula is executed, and
no amount is recomputed — this module can only ever show what the engine wrote.
If it disagreed with the payslip it would be a bug in the *renderer*, which is
why the categorised subtotals are reported alongside, never instead of, the
persisted `gross_amount` and `net_amount`.

TWO KINDS OF RULE METADATA, DELIBERATELY KEPT APART
---------------------------------------------------
`PayslipLine` copies the rule's `code`, `name` and `category` at compute time
precisely so a later rename cannot rewrite history (app/models/payroll.py). The
live `SalaryRule` row may since have been edited, recategorised or deleted. Both
are useful — "what ran" and "what the rule says today" — and conflating them is
how an explanation ends up describing a formula that never produced the number
next to it. So the line's own copy is authoritative here, and anything read from
the live rule is nested under `rule_definition_now` and labelled as current, not
historical.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import SalaryRuleCategory
from app.models.payroll import Payrun, Payslip
from app.models.salary import SalaryRule

#: How the seed inputs are described to a human (or a model) that has never
#: read Architecture §7. Keys absent from a payslip's snapshot are simply not
#: rendered — notably `LOP_AMOUNT`, which the engine OMITS rather than zeroes
#: when a schedule cannot be determined.
_INPUT_LABELS: dict[str, str] = {
    "CONTRACT_WAGE": "Contract wage for the period, from the contract this payslip was computed against",
    "WORKED_DAYS": "Distinct days with recorded attendance in the period",
    "UNPAID_LEAVE_DAYS": "Approved, payroll-integrated unpaid leave days in the period",
    "LOP_AMOUNT": "Loss of Pay deduction input, derived from wage, scheduled working days and unpaid leave",
}

#: Order the categories are presented in — the order a payslip reads on paper.
_CATEGORY_ORDER = (
    SalaryRuleCategory.BASIC,
    SalaryRuleCategory.ALLOWANCE,
    SalaryRuleCategory.GROSS,
    SalaryRuleCategory.DEDUCTION,
    SalaryRuleCategory.NET,
)


async def _live_rule_definitions(
    session: AsyncSession, rule_ids: set[int]
) -> dict[int, dict]:
    """Current definitions for the rules a payslip's lines still point at.

    A line whose `salary_rule_id` is NULL (the rule was deleted) or whose rule
    row has since vanished simply gets no entry — the line's own copied code,
    name, category and amount still explain what ran.
    """
    if not rule_ids:
        return {}
    rules = (
        (await session.execute(select(SalaryRule).where(SalaryRule.id.in_(rule_ids))))
        .scalars()
        .all()
    )
    return {
        rule.id: {
            "computation_method": rule.computation_method.value
            if hasattr(rule.computation_method, "value")
            else str(rule.computation_method),
            "expression": rule.expression,
            "amount": str(rule.amount) if rule.amount is not None else None,
            "percentage_base_code": rule.percentage_base_code,
            "is_active": rule.is_active,
            "current_name": rule.name,
            "current_category": rule.category.value
            if hasattr(rule.category, "value")
            else str(rule.category),
        }
        for rule in rules
    }


def _inputs_section(payslip: Payslip) -> dict:
    """The frozen computation inputs, or an explicit statement that they are gone."""
    snapshot = payslip.context_snapshot
    if snapshot is None:
        return {
            "available": False,
            "reason": (
                "This payslip predates the input snapshot (migration 018). Its original "
                "inputs are not recoverable, and reading the employee's current contract "
                "would describe today's wage, not the one this payslip was paid on."
            ),
            "values": [],
        }
    return {
        "available": True,
        "reason": None,
        "values": [
            {
                "name": key,
                "value": str(value),
                "description": _INPUT_LABELS.get(key, "Computation input recorded at compute time"),
            }
            for key, value in sorted(snapshot.items())
        ],
    }


async def calculation_tree(session: AsyncSession, payslip: Payslip) -> dict:
    """The full explainability payload for one payslip."""
    run = await session.get(Payrun, payslip.payrun_id)
    rule_ids = {line.salary_rule_id for line in payslip.lines if line.salary_rule_id}
    definitions = await _live_rule_definitions(session, rule_ids)

    lines = []
    subtotals: dict[str, Decimal] = {}
    for line in sorted(payslip.lines, key=lambda row: (row.sequence, row.code)):
        subtotals[line.category.value] = subtotals.get(line.category.value, Decimal("0.00")) + line.amount
        entry = {
            "code": line.code,
            "name": line.name,
            "category": line.category.value,
            "sequence": line.sequence,
            "amount": str(line.amount),
            "rule_still_exists": line.salary_rule_id is not None
            and line.salary_rule_id in definitions,
        }
        current = definitions.get(line.salary_rule_id) if line.salary_rule_id else None
        if current:
            entry["rule_definition_now"] = current
            entry["rule_renamed_since"] = current["current_name"] != line.name
            entry["rule_recategorised_since"] = current["current_category"] != line.category.value
        lines.append(entry)

    ordered_subtotals = [
        {"category": category.value, "total": str(subtotals[category.value])}
        for category in _CATEGORY_ORDER
        if category.value in subtotals
    ]

    return {
        "payslip_id": payslip.public_id,
        "payrun": (
            {
                "payrun_id": run.public_id,
                "name": run.name,
                "period_start": run.period_start.isoformat(),
                "period_end": run.period_end.isoformat(),
                "status": run.status.value,
            }
            if run
            else None
        ),
        "employee": (payslip.reference_snapshot or {}).get("employee"),
        "contract": (payslip.reference_snapshot or {}).get("contract"),
        "inputs": _inputs_section(payslip),
        "lines": lines,
        "category_subtotals": ordered_subtotals,
        "totals": {
            "worked_days": str(payslip.worked_days),
            # The persisted figures, which are the authoritative ones. The
            # subtotals above are a rendering of the same lines and are shown
            # so a reader can follow the arithmetic — not so anything can
            # override these two columns.
            "gross_amount": str(payslip.gross_amount),
            "net_amount": str(payslip.net_amount),
        },
        "warnings": list(payslip.warnings or []),
        "status": payslip.status.value,
        "source": "persisted PayslipLine rows and Payslip.context_snapshot; no rule was re-evaluated",
    }
