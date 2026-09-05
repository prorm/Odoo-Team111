"""Read-only contract history — the data behind "what changed in this
contract?" and PRD §5.8's Time Machine.

Every function here is a QUERY. Nothing in this module writes, and nothing in
it decides anything: the one piece of judgement involved — which contract a
payroll period resolves to — is delegated to
`payroll_context.resolve_period_contract`, the same function Architecture §7
step 1 uses when it actually computes a payslip. A second implementation of
"which contract applies" that agreed with the engine 99% of the time would be
worse than none at all, because the 1% would be a Time Machine that shows a
different contract than the one the employee was paid under.

Money crosses every boundary as a string (Architecture §6/§10). Callers here
are the AI context builder, the MCP read tools and Phase 10's timeline view —
all of which serialize to JSON, and `float(Decimal("30000.00"))` is exactly the
corruption §10 exists to prevent.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contract import Contract
from app.models.employee import Employee
from app.services.payroll_context import ContractResolutionError, resolve_period_contract


def _contract_row(contract: Contract) -> dict:
    """One contract, flattened for JSON. `wage` is a string, never a float."""
    return {
        "contract_id": contract.public_id,
        "wage": str(contract.wage),
        "status": contract.status.value,
        "start_date": contract.start_date.isoformat(),
        "end_date": contract.end_date.isoformat() if contract.end_date else None,
        "job_position": contract.job_position,
        "salary_structure": (
            {
                "code": contract.salary_structure.code,
                "name": contract.salary_structure.name,
            }
            if contract.salary_structure
            else None
        ),
        "working_schedule": (
            contract.working_schedule.name if contract.working_schedule else None
        ),
        "is_open_ended": contract.end_date is None,
    }


def _describe_change(previous: Contract, current: Contract) -> dict:
    """What changed between two consecutive contracts, stated as facts.

    The wage delta is a Decimal subtraction of two persisted `Numeric(12,2)`
    columns — arithmetic over stored evidence, not a payroll calculation. It
    never touches a Payslip: a payslip's wage is frozen in its own
    `context_snapshot` at compute time, and nothing here can change it.

    `percent_change` is deliberately omitted when the previous wage is zero
    rather than reported as some sentinel. A percentage of zero is undefined,
    and an invented "100%" is precisely the kind of number the AI would go on
    to narrate as if the ERP had asserted it.
    """
    delta = current.wage - previous.wage
    change: dict = {
        "from_contract_id": previous.public_id,
        "to_contract_id": current.public_id,
        "effective_date": current.start_date.isoformat(),
        "wage_before": str(previous.wage),
        "wage_after": str(current.wage),
        "wage_delta": str(delta),
        "direction": "increase" if delta > 0 else "decrease" if delta < 0 else "unchanged",
    }
    if previous.wage != 0:
        change["percent_change"] = str(
            (delta / previous.wage * Decimal("100")).quantize(Decimal("0.01"))
        )
    fields = []
    if previous.job_position != current.job_position:
        fields.append("job_position")
    if (previous.salary_structure_id or None) != (current.salary_structure_id or None):
        fields.append("salary_structure")
    if (previous.working_schedule_id or None) != (current.working_schedule_id or None):
        fields.append("working_schedule")
    change["other_changed_fields"] = fields
    return change


async def contract_timeline(
    session: AsyncSession,
    employee: Employee,
    *,
    include_deleted: bool = False,
) -> dict:
    """Every contract this employee has held, oldest first, plus the
    transitions between consecutive ones.

    Soft-deleted contracts are excluded by default and never contribute a
    transition: a deleted contract is not evidence of a wage change, and
    treating it as one would narrate a raise that was actually a typo someone
    withdrew.
    """
    conditions = [Contract.employee_id == employee.id]
    if not include_deleted:
        conditions.append(Contract.deleted_at.is_(None))
    contracts = list(
        (
            await session.execute(
                select(Contract)
                .where(*conditions)
                .order_by(Contract.start_date, Contract.id)
            )
        )
        .scalars()
        .all()
    )

    changes = [
        _describe_change(previous, current)
        for previous, current in zip(contracts, contracts[1:], strict=False)
    ]
    wage_changes = [c for c in changes if c["direction"] != "unchanged"]

    return {
        "employee_id": employee.public_id,
        "employee_name": employee.full_name,
        "contract_count": len(contracts),
        "contracts": [_contract_row(c) for c in contracts],
        "transitions": changes,
        "wage_changes": wage_changes,
        "has_wage_change": bool(wage_changes),
    }


async def contract_for_period(
    session: AsyncSession,
    employee: Employee,
    period_start: date,
    period_end: date,
) -> dict:
    """Which contract a payroll period resolves to — the Time Machine's
    "selecting a period highlights the contract it actually resolves to".

    Delegates to the engine's own resolver, so an unresolvable period is
    reported with the engine's own words rather than a second opinion. A
    failure here is information, not an error: "no single applicable contract"
    is one of the most useful things a payroll question can be answered with.
    """
    try:
        contract = await resolve_period_contract(session, employee, period_start, period_end)
    except ContractResolutionError as exc:
        return {
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "resolved": False,
            "reason": exc.reason,
            "contract": None,
        }
    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "resolved": True,
        "reason": None,
        "contract": _contract_row(contract),
        "covers_whole_period": contract.start_date <= period_start
        and (contract.end_date is None or contract.end_date >= period_end),
    }


async def expiring_contracts(
    session: AsyncSession,
    *,
    on: date,
    within_days: int = 60,
) -> list[dict]:
    """Active contracts whose `end_date` falls inside the window.

    A deterministic query, not a judgement: "expiring soon" is defined
    entirely by `within_days`, which the caller states. Contracts that already
    ended are included when they ended after `on - within_days` is irrelevant —
    they are excluded, because an expired contract is a different problem from
    an expiring one and conflating them makes the count meaningless.
    """
    from datetime import timedelta

    from app.models.enums import ContractStatus

    horizon = on + timedelta(days=within_days)
    rows = (
        (
            await session.execute(
                select(Contract)
                .where(
                    Contract.deleted_at.is_(None),
                    Contract.status == ContractStatus.ACTIVE,
                    Contract.end_date.is_not(None),
                    Contract.end_date >= on,
                    Contract.end_date <= horizon,
                )
                .order_by(Contract.end_date)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            **_contract_row(contract),
            "employee_id": contract.employee.public_id,
            "employee_name": contract.employee.full_name,
            "days_remaining": (contract.end_date - on).days,
        }
        for contract in rows
    ]


async def find_contract_conflicts(
    session: AsyncSession, *, employee: Optional[Employee] = None
) -> list[dict]:
    """Active contracts of one employee whose inclusive date ranges overlap.

    This should always return an empty list. `contracts_active_period_overlap_excl`
    (Architecture §6) makes an overlap impossible to insert, so a non-empty
    result means the EXCLUDE constraint has been dropped or disabled — which is
    exactly why the check is worth running rather than assuming. The comparison
    uses inclusive bounds on both ends, matching the constraint's `'[]'` range,
    so a single shared day counts as a conflict here for the same reason it is
    rejected there.
    """
    from app.models.enums import ContractStatus

    conditions = [
        Contract.deleted_at.is_(None),
        Contract.status == ContractStatus.ACTIVE,
    ]
    if employee is not None:
        conditions.append(Contract.employee_id == employee.id)

    rows = list(
        (
            await session.execute(
                select(Contract).where(*conditions).order_by(Contract.employee_id, Contract.start_date)
            )
        )
        .scalars()
        .all()
    )

    by_employee: dict[int, list[Contract]] = {}
    for contract in rows:
        by_employee.setdefault(contract.employee_id, []).append(contract)

    conflicts: list[dict] = []
    for contracts in by_employee.values():
        for index, first in enumerate(contracts):
            for second in contracts[index + 1 :]:
                first_end = first.end_date or date.max
                second_end = second.end_date or date.max
                if first.start_date <= second_end and second.start_date <= first_end:
                    conflicts.append(
                        {
                            "employee_id": first.employee.public_id,
                            "employee_name": first.employee.full_name,
                            "contract_ids": [first.public_id, second.public_id],
                            "message": (
                                f"{first.employee.full_name} has two active contracts with "
                                f"overlapping periods ({first.public_id} and {second.public_id}). "
                                "The non-overlap constraint should make this impossible — check "
                                "that contracts_active_period_overlap_excl is still present."
                            ),
                        }
                    )
    return conflicts
