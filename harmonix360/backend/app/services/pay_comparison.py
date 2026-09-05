"""Read-only period-over-period payslip comparison (PRD §5.9).

This module answers "what changed between last month's pay and this month's?"
using nothing but rows the rule engine already wrote. It does not evaluate a
salary rule, does not read a live Contract, and cannot produce a figure that
disagrees with a payslip — every number it reports is either copied from a
persisted column or is the difference of two such columns.

That distinction is the whole design. A comparison that RE-DERIVED an amount
would be a second payroll engine with a friendlier name, and the first time it
drifted from the real one it would tell an employee their payslip was wrong.
Here, if the two payslips disagree with each other, the diff faithfully reports
that they disagree.

INPUTS COME FROM THE SNAPSHOT, NOT FROM TODAY'S DATA
---------------------------------------------------
`Payslip.context_snapshot` holds the exact Decimal inputs (`CONTRACT_WAGE`,
`WORKED_DAYS`, `UNPAID_LEAVE_DAYS`, `LOP_AMOUNT`) that produced the payslip,
frozen at compute (migration 018). Reading the employee's contract *now* to
explain a payslip computed in July would attribute July's pay to August's wage
— which is exactly the bug migration 018 exists to prevent. A legacy payslip
with no snapshot is reported as unavailable rather than reconstructed.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.models.payroll import Payrun, Payslip

#: The snapshot keys worth diffing, in the order a reader should meet them.
#: `LOP_AMOUNT` is absent from the snapshot entirely when the schedule could not
#: be determined (Architecture §7's "never substitute zero"), and this module
#: preserves that absence rather than defaulting it.
_TRACKED_INPUTS = ("CONTRACT_WAGE", "WORKED_DAYS", "UNPAID_LEAVE_DAYS", "LOP_AMOUNT")


def _decimal_or_none(raw) -> Optional[Decimal]:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (ArithmeticError, ValueError):
        return None


def _delta(before: Optional[Decimal], after: Optional[Decimal]) -> Optional[dict]:
    """A signed difference, or None when either side is unknown.

    None is returned rather than treating a missing value as zero. "We do not
    know last month's LOP" and "last month's LOP was zero" are different facts,
    and only one of them supports the sentence "the LOP is what changed".
    """
    if before is None or after is None:
        return None
    difference = after - before
    return {
        "before": str(before),
        "after": str(after),
        "delta": str(difference),
        "direction": "increase" if difference > 0 else "decrease" if difference < 0 else "unchanged",
    }


async def previous_payslip(
    session: AsyncSession, payslip: Payslip
) -> Optional[Payslip]:
    """The same employee's most recent payslip from a period ending strictly
    before this one starts.

    "Strictly before" rather than "any earlier payrun": two runs over the same
    period are a duplicate-payslip situation, and diffing a payslip against its
    own duplicate would report every figure as unchanged and explain nothing.
    Soft-deleted runs and payslips are excluded — a deleted run is not history.
    """
    current_run = await session.get(Payrun, payslip.payrun_id)
    if current_run is None:  # pragma: no cover - FK guarantees the row exists
        return None

    stmt = (
        select(Payslip)
        .join(Payrun, Payslip.payrun_id == Payrun.id)
        .where(
            Payslip.employee_id == payslip.employee_id,
            Payslip.id != payslip.id,
            Payslip.deleted_at.is_(None),
            Payrun.deleted_at.is_(None),
            Payrun.period_end < current_run.period_start,
        )
        .order_by(Payrun.period_end.desc(), Payslip.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


def _lines_by_code(payslip: Payslip) -> dict[str, dict]:
    return {
        line.code: {
            "code": line.code,
            "name": line.name,
            "category": line.category.value,
            "sequence": line.sequence,
            "amount": str(line.amount),
        }
        for line in payslip.lines
    }


def _compare_lines(before: Payslip, after: Payslip) -> list[dict]:
    """Per-rule differences, including rules that appeared or disappeared.

    A rule present in one payslip and absent from the other is reported as
    `added`/`removed` rather than as a change against an implied zero. A rule
    that did not run did not contribute zero — it did not contribute, and the
    reason (a structure change, an inactive rule) is a different investigation
    than "the amount moved".
    """
    old, new = _lines_by_code(before), _lines_by_code(after)
    rows: list[dict] = []
    for code in sorted(set(old) | set(new), key=lambda c: (new.get(c) or old[c])["sequence"]):
        old_line, new_line = old.get(code), new.get(code)
        if old_line and new_line:
            difference = Decimal(new_line["amount"]) - Decimal(old_line["amount"])
            if difference == 0:
                continue
            rows.append(
                {
                    "code": code,
                    "name": new_line["name"],
                    "category": new_line["category"],
                    "change": "changed",
                    "before": old_line["amount"],
                    "after": new_line["amount"],
                    "delta": str(difference),
                    "direction": "increase" if difference > 0 else "decrease",
                }
            )
        elif new_line:
            rows.append(
                {
                    "code": code,
                    "name": new_line["name"],
                    "category": new_line["category"],
                    "change": "added",
                    "before": None,
                    "after": new_line["amount"],
                    "delta": None,
                    "direction": None,
                }
            )
        else:
            rows.append(
                {
                    "code": code,
                    "name": old_line["name"],
                    "category": old_line["category"],
                    "change": "removed",
                    "before": old_line["amount"],
                    "after": None,
                    "delta": None,
                    "direction": None,
                }
            )
    return rows


def _period_of(run: Payrun) -> dict:
    return {
        "payrun_id": run.public_id,
        "name": run.name,
        "period_start": run.period_start.isoformat(),
        "period_end": run.period_end.isoformat(),
        "status": run.status.value,
    }


async def compare_with_previous(session: AsyncSession, payslip: Payslip) -> dict:
    """The full diff for one payslip against its predecessor.

    Returns a `comparable: False` envelope rather than raising when there is no
    earlier payslip — a first payslip is a normal state, not an error, and the
    caller (an AI prompt, a UI panel) needs to say "there is nothing to compare
    against" rather than show an empty table.
    """
    current_run = await session.get(Payrun, payslip.payrun_id)
    earlier = await previous_payslip(session, payslip)

    if earlier is None:
        return {
            "comparable": False,
            "reason": (
                "This is the earliest payslip on record for this employee, so there is no "
                "previous period to compare it against."
            ),
            "current": {
                "payslip_id": payslip.public_id,
                "period": _period_of(current_run) if current_run else None,
                "gross_amount": str(payslip.gross_amount),
                "net_amount": str(payslip.net_amount),
            },
            "previous": None,
        }

    earlier_run = await session.get(Payrun, earlier.payrun_id)
    current_inputs = payslip.context_snapshot or {}
    earlier_inputs = earlier.context_snapshot or {}

    unavailable: list[str] = []
    if payslip.context_snapshot is None:
        unavailable.append(
            f"Input snapshot missing for the current payslip {payslip.public_id}; its "
            "computation inputs cannot be shown without inventing them from live data."
        )
    if earlier.context_snapshot is None:
        unavailable.append(
            f"Input snapshot missing for the previous payslip {earlier.public_id}; input "
            "changes between the two periods cannot be established."
        )

    input_changes: dict[str, Optional[dict]] = {}
    for key in _TRACKED_INPUTS:
        before = _decimal_or_none(earlier_inputs.get(key))
        after = _decimal_or_none(current_inputs.get(key))
        change = _delta(before, after)
        if change is None:
            if before is None and after is None:
                continue
            input_changes[key] = {
                "before": str(before) if before is not None else None,
                "after": str(after) if after is not None else None,
                "delta": None,
                "direction": "unknown",
            }
            unavailable.append(
                f"{key} is present in only one of the two periods, so its contribution to "
                "the change cannot be quantified."
            )
        else:
            input_changes[key] = change

    return {
        "comparable": True,
        "reason": None,
        "current": {
            "payslip_id": payslip.public_id,
            "period": _period_of(current_run) if current_run else None,
            "contract_id": (payslip.reference_snapshot or {}).get("contract", {}).get("id"),
            "worked_days": str(payslip.worked_days),
            "gross_amount": str(payslip.gross_amount),
            "net_amount": str(payslip.net_amount),
            "status": payslip.status.value,
        },
        "previous": {
            "payslip_id": earlier.public_id,
            "period": _period_of(earlier_run) if earlier_run else None,
            "contract_id": (earlier.reference_snapshot or {}).get("contract", {}).get("id"),
            "worked_days": str(earlier.worked_days),
            "gross_amount": str(earlier.gross_amount),
            "net_amount": str(earlier.net_amount),
            "status": earlier.status.value,
        },
        "totals": {
            "worked_days": _delta(earlier.worked_days, payslip.worked_days),
            "gross_amount": _delta(earlier.gross_amount, payslip.gross_amount),
            "net_amount": _delta(earlier.net_amount, payslip.net_amount),
        },
        "input_changes": input_changes,
        "line_changes": _compare_lines(earlier, payslip),
        "contract_changed": (
            (payslip.contract_id != earlier.contract_id)
        ),
        "unavailable": unavailable,
    }


async def payroll_trend(
    session: AsyncSession, employee: Employee, *, limit: int = 6
) -> list[dict]:
    """This employee's last `limit` payslips, oldest first.

    A flat series of persisted totals. No smoothing, no projection, no
    "expected" figure — the point is to let a reader (or a model) see the shape
    of what actually happened, not to assert a trend line the ERP never
    computed.
    """
    rows = (
        (
            await session.execute(
                select(Payslip, Payrun)
                .join(Payrun, Payslip.payrun_id == Payrun.id)
                .where(
                    Payslip.employee_id == employee.id,
                    Payslip.deleted_at.is_(None),
                    Payrun.deleted_at.is_(None),
                )
                .order_by(Payrun.period_end.desc())
                .limit(limit)
            )
        )
        .all()
    )
    series = [
        {
            "payslip_id": payslip.public_id,
            "period_start": run.period_start.isoformat(),
            "period_end": run.period_end.isoformat(),
            "payrun_status": run.status.value,
            "worked_days": str(payslip.worked_days),
            "gross_amount": str(payslip.gross_amount),
            "net_amount": str(payslip.net_amount),
        }
        for payslip, run in rows
    ]
    series.reverse()
    return series
