"""Architecture §7 steps 1, 3 and 6 — everything the rule engine needs BEFORE
`resolve_salary_structure` runs, and the deterministic warning checks that run
after it.

Deliberately session-light and free of HTTP: the two functions that matter
(`build_seed_context`, `warning_checks`) take plain rows and return plain
values, so the payslip a payrun produces can be reasoned about — and tested —
without a request in flight. `app/services/payroll.py` owns the transaction,
the advisory lock and the writes; this module owns *what the numbers mean*.

Nothing here computes money. Money is `resolve_salary_structure`'s job
(Phase 3, `app/services/salary_resolver.py`) and this phase does not
reimplement one line of it. What this module produces is the `seed_context`
that function consumes: exactly the names in `SEED_CONTEXT_NAMES`, every value
a `Decimal`.

THE THREE SEMANTICS THIS FILE DECIDES
-------------------------------------
Phases 1-3 fixed most of payroll's meaning already; these three were genuinely
open, and each is stated here rather than left implicit in a query:

1. **WORKED_DAYS counts DAYS, not hours.** One distinct UTC calendar date with
   at least one attendance record whose RE-DERIVED status is neither `absent`
   nor `missing_checkout` counts as one day. Re-derived, never read from
   `Attendance.status`: Phase 2's handoff is explicit that a stored status can
   be stale (an entry left open yesterday is still `present` in the column but
   is `missing_checkout` in fact), and payroll must not pay from a stale
   status. `missing_checkout` does not count because its `worked_hours` is
   NULL — we do not know the day was worked, and asserting either way would be
   inventing. It also raises a BLOCKING warning, so a human resolves it before
   the run is finalized rather than silently losing or gaining a day.

   Hours are deliberately absent: `SEED_CONTEXT_NAMES` is a fixed vocabulary
   (see its docstring), and adding `WORKED_HOURS` is a deliberate change to
   that constant in agreement with whoever authors salary structures — not
   something this phase slips in.

2. **UNPAID_LEAVE_DAYS counts calendar days of overlap, deduplicated.** For
   every APPROVED request whose type has `payroll_integration=True`, the days
   of `[date_from, date_to] ∩ [period_start, period_end]` go into a set, and
   the set's size is the value. A set, so two approved requests covering the
   same day cannot double-count it. Calendar days regardless of the type's
   unit: `TimeOffType.unit` governs what an ALLOCATION is denominated in, and
   Phase 2 refused to invent an 8-hours-to-a-day conversion — counting the
   days an absence actually spans needs no such conversion, while summing
   `duration` across a `days` type and an `hours` type would silently add two
   different units together.

   `TimeOffRequest.duration` is not used at all here, for a second reason
   beyond units: a request may start before or end after the payrun's period,
   and its duration covers the whole request, not the part inside the period.

3. **Attendance and leave are reported side by side, never netted.** A day
   that is both attended and on approved leave counts in both numbers. How to
   combine them is the salary structure author's decision, expressed in a
   formula — not a policy this module hardcodes into the inputs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Iterable, Optional, Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attendance import Attendance
from app.models.contract import Contract
from app.models.employee import Employee
from app.models.enums import AttendanceStatus, ContractStatus, TimeOffRequestStatus
from app.models.payroll import Payrun, Payslip
from app.models.time_off import TimeOffRequest, TimeOffType
from app.services.attendance import derive_attendance_status, schedule_expectations

#: Severity vocabulary for `PayrollWarning`. BLOCKING stops Validate (PRD
#: §5.10's pre-finalization gate); ADVISORY is surfaced and does not.
BLOCKING = "blocking"
ADVISORY = "advisory"


@dataclass(frozen=True)
class PayrollWarning:
    """One deterministic finding from Architecture §7 step 6.

    Deterministic is the whole point: every field below comes from a query or a
    comparison, never from a model's opinion. PRD §5.9 allows AI to *narrate*
    these afterwards; nothing here is AI-authored, and no AI code path can
    write one.
    """

    code: str
    severity: str
    message: str
    #: Public ids of the records a user should open to fix this — PRD §5.10's
    #: "navigation straight to the offending records".
    references: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        """JSONB-ready. Plain built-ins only: this is persisted into
        `Payslip.warnings` and read back by the validation firewall's
        aggregate queries."""
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "references": list(self.references),
        }

    @property
    def is_blocking(self) -> bool:
        return self.severity == BLOCKING


@dataclass(frozen=True)
class AttendanceFacts:
    """What Attendance says about one employee over one period."""

    worked_days: Decimal
    missing_checkout_dates: tuple[date, ...] = ()
    #: Distinct dates that counted toward `worked_days`. Kept for the
    #: explainability tree (PRD §5.6) and for tests that need to see WHICH
    #: days were counted, not just how many.
    worked_dates: tuple[date, ...] = ()


@dataclass(frozen=True)
class LeaveFacts:
    """What approved, payroll-integrated Time Off says about one employee over
    one period."""

    unpaid_leave_days: Decimal
    request_public_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PayrollContext:
    """Everything step 4 needs, plus the provenance steps 5-6 report on."""

    seed: dict[str, Decimal] = field(default_factory=dict)
    attendance: AttendanceFacts = field(default_factory=lambda: AttendanceFacts(Decimal("0.00")))
    leave: LeaveFacts = field(default_factory=lambda: LeaveFacts(Decimal("0.00")))


class ContractResolutionError(Exception):
    """No single applicable contract for `(employee, period)` — Architecture §7
    step 1 could not be satisfied, so there is nothing to compute a payslip
    from. Carries the employee so the caller can report which one."""

    def __init__(self, employee: Employee, reason: str):
        self.employee = employee
        self.reason = reason
        super().__init__(reason)


def period_days(start: date, end: date) -> list[date]:
    """Every calendar date in `[start, end]`, inclusive at both ends —
    matching the Contract EXCLUDE constraint's `'[]'` bounds and Phase 2's
    inclusive leave-duration convention. One convention for "a period", used
    everywhere, so a boundary day is never counted by one rule and skipped by
    another."""
    if end < start:
        return []
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


# --------------------------------------------------------------------------
# Step 1 — resolve the one applicable contract
# --------------------------------------------------------------------------


async def resolve_period_contract(
    session: AsyncSession, employee: Employee, period_start: date, period_end: date
) -> Contract:
    """The ONE active contract overlapping the payrun's period.

    Uniqueness is a database guarantee, not an application one:
    `contracts_active_period_overlap_excl` forbids two active contracts of the
    same employee from overlapping, with inclusive `'[]'` bounds
    (app/models/contract.py). This query therefore selects the OVERLAPPING
    active contract and expects at most one back.

    The `> 1` branch is still written, and still raises. It should be
    unreachable while the constraint exists; if it is ever reached, the
    constraint has been dropped or disabled, and computing a payslip from an
    arbitrary one of two candidates would be the worst possible response —
    silently paying someone under rules nobody chose.

    A contract that only PARTIALLY covers the period is returned normally and
    flagged by the `contract_gap` warning. It is genuinely the applicable
    contract; whether a part-period contract should be prorated is the salary
    structure's business (via WORKED_DAYS), not this function's.
    """
    stmt = (
        select(Contract)
        .where(
            Contract.employee_id == employee.id,
            Contract.status == ContractStatus.ACTIVE,
            Contract.deleted_at.is_(None),
            Contract.start_date <= period_end,
            or_(Contract.end_date.is_(None), Contract.end_date >= period_start),
        )
        .order_by(Contract.start_date, Contract.id)
    )
    candidates = list((await session.execute(stmt)).scalars().all())

    if not candidates:
        raise ContractResolutionError(
            employee,
            f"{employee.full_name} has no active contract covering "
            f"{period_start} to {period_end}; payroll cannot resolve what they are paid",
        )
    if len(candidates) > 1:
        found = ", ".join(c.public_id for c in candidates)
        raise ContractResolutionError(
            employee,
            f"{employee.full_name} has {len(candidates)} active contracts overlapping "
            f"{period_start} to {period_end} ({found}). Payroll must resolve exactly one "
            "contract per period — the non-overlap constraint should have made this "
            "impossible, so check that it is still present.",
        )
    return candidates[0]


# --------------------------------------------------------------------------
# Step 3 — build the computation context from Attendance + approved Time Off
# --------------------------------------------------------------------------


async def attendance_facts(
    session: AsyncSession,
    employee: Employee,
    period_start: date,
    period_end: date,
    *,
    schedule=None,
    now: Optional[datetime] = None,
) -> AttendanceFacts:
    """Worked days and missing checkouts, re-deriving every status.

    `schedule` is the contract's working-schedule override when it has one,
    falling back to the employee's default (PS A3, and the note on
    `Contract.working_schedule_id`). It only affects the late/overtime
    thresholds `derive_attendance_status` applies — neither of which changes
    the day COUNT — but passing the right one keeps this derivation identical
    to what the Attendance screen shows, which is the point of reusing the
    Phase 2 function rather than writing a second status policy here.
    """
    now = now or datetime.now(UTC)

    # Half-open on the upper bound in UTC: `check_in` is a timestamp, and a
    # `<= period_end` comparison against a date would drop everything after
    # midnight on the last day of the period.
    window_start = datetime.combine(period_start, datetime.min.time(), UTC)
    window_end = datetime.combine(period_end + timedelta(days=1), datetime.min.time(), UTC)

    stmt = (
        select(Attendance)
        .where(
            Attendance.employee_id == employee.id,
            Attendance.deleted_at.is_(None),
            Attendance.check_in >= window_start,
            Attendance.check_in < window_end,
        )
        .order_by(Attendance.check_in)
    )
    rows = list((await session.execute(stmt)).scalars().all())

    worked: set[date] = set()
    missing: set[date] = set()

    for row in rows:
        day = row.check_in.astimezone(UTC).date()
        expected_start, expected_hours = schedule_expectations(employee, day, schedule=schedule)
        status = derive_attendance_status(
            row.check_in,
            row.check_out,
            now=now,
            expected_start=expected_start,
            expected_hours=expected_hours,
        )
        if status == AttendanceStatus.MISSING_CHECKOUT:
            missing.add(day)
        elif status != AttendanceStatus.ABSENT:
            worked.add(day)

    # A day with both a complete record and a separate open one counts as
    # worked AND raises the missing-checkout warning: the completed record is
    # evidence of work, and the open one is still a record a human must close.
    return AttendanceFacts(
        worked_days=Decimal(len(worked)).quantize(Decimal("0.01")),
        missing_checkout_dates=tuple(sorted(missing)),
        worked_dates=tuple(sorted(worked)),
    )


async def leave_facts(
    session: AsyncSession, employee: Employee, period_start: date, period_end: date
) -> LeaveFacts:
    """Approved, payroll-integrated leave days falling inside the period.

    `payroll_integration` is the gate (`TimeOffType.payroll_integration`: "when
    true, approved absences of this type reach the payroll computation
    context"). A type without it is invisible here no matter how much of the
    period it covers — which is exactly what makes paid annual leave and
    unpaid absence distinguishable without a second flag.

    Only `approved` counts. Phase 2 is explicit that a pending request
    reserves nothing; a request awaiting a decision must not reduce pay.
    """
    stmt = (
        select(TimeOffRequest)
        .join(TimeOffType, TimeOffRequest.time_off_type_id == TimeOffType.id)
        .where(
            TimeOffRequest.employee_id == employee.id,
            TimeOffRequest.deleted_at.is_(None),
            TimeOffRequest.status == TimeOffRequestStatus.APPROVED,
            TimeOffType.payroll_integration.is_(True),
            # Inclusive overlap, matching Phase 2's inclusive [date_from,
            # date_to] request semantics.
            TimeOffRequest.date_from <= period_end,
            TimeOffRequest.date_to >= period_start,
        )
        .order_by(TimeOffRequest.date_from, TimeOffRequest.id)
    )
    requests = list((await session.execute(stmt)).scalars().all())

    days: set[date] = set()
    for request in requests:
        overlap_start = max(request.date_from, period_start)
        overlap_end = min(request.date_to, period_end)
        days.update(period_days(overlap_start, overlap_end))

    return LeaveFacts(
        unpaid_leave_days=Decimal(len(days)).quantize(Decimal("0.01")),
        request_public_ids=tuple(r.public_id for r in requests),
    )


async def build_payroll_context(
    session: AsyncSession,
    employee: Employee,
    contract: Contract,
    period_start: date,
    period_end: date,
    *,
    now: Optional[datetime] = None,
) -> PayrollContext:
    """Architecture §7 step 3, assembled.

    The returned `seed` dict has EXACTLY the keys in `SEED_CONTEXT_NAMES` and
    every value is a `Decimal`. It is passed verbatim to
    `resolve_salary_structure`; a structure's formulas may reference any of
    these names, and `validate_structure_rule_order` already refused, at save
    time, any structure referencing a name outside this set.
    """
    facts = await attendance_facts(
        session,
        employee,
        period_start,
        period_end,
        schedule=contract.working_schedule,
        now=now,
    )
    leave = await leave_facts(session, employee, period_start, period_end)

    return PayrollContext(
        seed={
            "WORKED_DAYS": facts.worked_days,
            # Numeric(12,2) on the column; quantized here so a wage that
            # arrived with more places (it cannot from the DB, but can from a
            # hand-built object in a test) cannot widen every downstream
            # amount.
            "CONTRACT_WAGE": Decimal(contract.wage).quantize(Decimal("0.01")),
            "UNPAID_LEAVE_DAYS": leave.unpaid_leave_days,
        },
        attendance=facts,
        leave=leave,
    )


# --------------------------------------------------------------------------
# Step 6 — the deterministic warning checks
# --------------------------------------------------------------------------


async def overlapping_payslip_ids(
    session: AsyncSession,
    employee: Employee,
    period_start: date,
    period_end: date,
    *,
    exclude_payrun_id: int,
) -> list[str]:
    """Payslips this employee already holds from a DIFFERENT payrun whose
    period overlaps this one — PRD §5.9's "duplicate payslip attempt".

    Scoped to other payruns on purpose. Recomputing THIS payrun replaces its
    own payslips wholesale, so its own rows are never a duplicate of
    themselves; two runs covering the same fortnight are.
    """
    stmt = (
        select(Payslip.public_id)
        .join(Payrun, Payslip.payrun_id == Payrun.id)
        .where(
            Payslip.employee_id == employee.id,
            Payslip.deleted_at.is_(None),
            Payslip.payrun_id != exclude_payrun_id,
            Payrun.deleted_at.is_(None),
            Payrun.period_start <= period_end,
            Payrun.period_end >= period_start,
        )
        .order_by(Payslip.id)
    )
    return list((await session.execute(stmt)).scalars().all())


def warning_checks(
    *,
    employee: Employee,
    contract: Contract,
    payrun: Payrun,
    context: PayrollContext,
    duplicate_payslip_ids: Sequence[str],
) -> list[PayrollWarning]:
    """Architecture §7 step 6, as a pure function over already-loaded rows.

    Pure so the firewall's behaviour is testable without a payrun in the
    database, and so the same checks can later be run in "preview" mode by
    PRD §5.10's Revalidate action without recomputing anything.
    """
    warnings: list[PayrollWarning] = []

    # -- missing bank details (PRD §5.10, blocking) -------------------------
    # `bank_account` is a plain account string (PRD §8's stated assumption).
    # Blank-but-present counts as missing: a whitespace string is not somewhere
    # money can be sent.
    if not (employee.bank_account or "").strip():
        warnings.append(
            PayrollWarning(
                code="missing_bank_details",
                severity=BLOCKING,
                message=f"{employee.full_name} has no bank account on file, so this payslip cannot be paid out.",
                references=(employee.public_id,),
            )
        )

    # -- missing checkout (PRD §5.10, blocking) ----------------------------
    if context.attendance.missing_checkout_dates:
        listed = ", ".join(str(day) for day in context.attendance.missing_checkout_dates)
        warnings.append(
            PayrollWarning(
                code="missing_checkout",
                severity=BLOCKING,
                message=(
                    f"{employee.full_name} has attendance without a check-out on {listed}. "
                    "Those days are not counted as worked until an HR correction closes them."
                ),
                references=(employee.public_id,),
            )
        )

    # -- contract gap (Architecture §7 step 6, blocking) -------------------
    covers_start = contract.start_date <= payrun.period_start
    covers_end = contract.end_date is None or contract.end_date >= payrun.period_end
    if not (covers_start and covers_end):
        covered = f"{contract.start_date} to {contract.end_date or 'open-ended'}"
        warnings.append(
            PayrollWarning(
                code="contract_gap",
                severity=BLOCKING,
                message=(
                    f"Contract {contract.public_id} covers {covered}, which does not span the whole "
                    f"payrun period {payrun.period_start} to {payrun.period_end}. Part of the period "
                    "has no contract behind it."
                ),
                references=(contract.public_id,),
            )
        )

    # -- duplicate payslip (PRD §5.9 / §5.10, blocking) --------------------
    if duplicate_payslip_ids:
        warnings.append(
            PayrollWarning(
                code="duplicate_payslip",
                severity=BLOCKING,
                message=(
                    f"{employee.full_name} already has a payslip for an overlapping period "
                    f"({', '.join(duplicate_payslip_ids)}). Paying both would pay the period twice."
                ),
                references=tuple(duplicate_payslip_ids),
            )
        )

    # -- structure mismatch (advisory) -------------------------------------
    # A payrun executes ONE structure — its own (`Payrun` is "one execution of
    # one SalaryStructure over one period", app/models/payroll.py) — so a
    # contract naming a different one does not change what runs. It is
    # surfaced rather than silently resolved either way, because the two
    # readings disagree only when someone has configured them to.
    if (
        contract.salary_structure_id is not None
        and contract.salary_structure_id != payrun.salary_structure_id
    ):
        warnings.append(
            PayrollWarning(
                code="structure_mismatch",
                severity=ADVISORY,
                message=(
                    f"Contract {contract.public_id} names a different salary structure than this "
                    "payrun. The payrun's structure was executed; check that this run is the one "
                    "you intended for this employee."
                ),
                references=(contract.public_id,),
            )
        )

    # -- no attendance recorded (PRD §5.9 "low attendance", advisory) ------
    # Advisory, not blocking: a genuinely absent month is a real payroll
    # outcome, and structures that do not reference WORKED_DAYS are unaffected.
    if context.attendance.worked_days == 0:
        warnings.append(
            PayrollWarning(
                code="no_attendance",
                severity=ADVISORY,
                message=(
                    f"{employee.full_name} has no counted attendance days in this period. "
                    "Any rule using WORKED_DAYS computed against zero."
                ),
                references=(employee.public_id,),
            )
        )

    return warnings


def blocking_summary(warnings: Iterable[dict]) -> dict[str, int]:
    """Count blocking findings by code — PRD §5.10's "blocking issue count,
    categorized". Takes the persisted JSONB dicts rather than
    `PayrollWarning`s, because the firewall reads what was stored, not what
    was computed in some earlier process."""
    counts: dict[str, int] = {}
    for entry in warnings or []:
        if entry.get("severity") == BLOCKING:
            counts[entry["code"]] = counts.get(entry["code"], 0) + 1
    return counts
