"""Deterministic anomaly detection (PRD §5.7, Architecture §8.6).

Seven checks, each a query with an explicit threshold. Every one answers a
question of the form "is this number outside this stated bound", and the bound
is a named constant at the top of this file rather than a judgement made
somewhere in the middle of a function.

    AN LLM NEVER DECIDES WHETHER A PAYROLL TRANSACTION IS VALID (PRD §5.7).

That is the whole design constraint. The AI layer may narrate what is found
here; it cannot add to it, remove from it, or change a severity. Consequently
every `Anomaly` carries the comparison that produced it — `current_value`,
`baseline`, `reason` — so a reader can check the arithmetic rather than trust
the label, and so the narration has something concrete to explain.

READ-ONLY, AND NOT A SECOND WARNING SYSTEM
------------------------------------------
Nothing here writes. In particular, nothing here writes to `Payslip.warnings`:
those are the payroll engine's own §7-step-6 findings, they gate Validate, and
a detector that could add to them would be an anomaly engine with the power to
block payroll. `missing_bank_details` and `missing_checkout` appear in both
places on purpose — this module re-derives them from the source records so the
dashboard can show them for employees who have no payslip yet, while the
firewall reads the persisted ones that actually gate a specific payrun.

THRESHOLDS ARE CONVENTIONS, AND ARE LABELLED AS SUCH
----------------------------------------------------
"Large" salary jump and "low" attendance are not facts about payroll; they are
choices. Each threshold is stated in the anomaly's `reason` string so nobody
downstream — human or model — mistakes a 20% bound for a law.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attendance import Attendance
from app.models.contract import Contract
from app.models.employee import Employee
from app.models.enums import AttendanceStatus, ContractStatus, EmployeeStatus
from app.models.payroll import Payrun, Payslip
from app.services.dashboard import ResolvedFilters, _employee_conditions, _period_overlaps_payrun

# --------------------------------------------------------------------------
# Thresholds. Conventions, not laws — each is quoted into the reason string.
# --------------------------------------------------------------------------

#: A net-pay movement beyond this, period over period, is worth a look.
SALARY_JUMP_PCT = Decimal("20.00")
#: Attendance below this fraction of scheduled working days.
LOW_ATTENDANCE_PCT = Decimal("70.00")
#: Hours in a single attendance record beyond a normal long day.
OVERTIME_HOURS = Decimal("10.00")
#: How far ahead a contract end date counts as "expiring".
CONTRACT_EXPIRY_DAYS = 60
#: A department's total net pay moving more than this, period over period.
DEPARTMENT_SPIKE_PCT = Decimal("15.00")

SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"


@dataclass(frozen=True)
class Anomaly:
    """One finding, with the evidence that produced it.

    `navigate_to` is the record a person should open to act on this — PRD
    §5.10's "navigation straight to the offending records", applied to
    anomalies. Without it a dashboard card states a problem and leaves the
    reader to search for its subject.
    """

    type: str
    severity: str
    message: str
    reason: str
    employee_id: Optional[str] = None
    employee_name: Optional[str] = None
    department: Optional[str] = None
    period: Optional[str] = None
    current_value: Optional[str] = None
    baseline: Optional[str] = None
    navigate_to: Optional[str] = None
    references: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict:
        """JSON-ready. Every numeric field is already a string — these travel
        into AI prompts and WebSocket frames, and Architecture §10 forbids a
        float on any of those boundaries."""
        return {
            "type": self.type,
            "severity": self.severity,
            "message": self.message,
            "reason": self.reason,
            "employee_id": self.employee_id,
            "employee_name": self.employee_name,
            "department": self.department,
            "period": self.period,
            "current_value": self.current_value,
            "baseline": self.baseline,
            "navigate_to": self.navigate_to,
            "references": list(self.references),
        }


def _pct_change(before: Decimal, after: Decimal) -> Optional[Decimal]:
    """Percentage movement, or None when the baseline is zero.

    None rather than a sentinel: a percentage of zero is undefined, and an
    invented "100%" would be reported as a detected anomaly with a fabricated
    magnitude attached to it.
    """
    if before == 0:
        return None
    return ((after - before) / before * Decimal("100")).quantize(Decimal("0.01"))


# --------------------------------------------------------------------------
# Detectors
# --------------------------------------------------------------------------


async def detect_large_salary_jump(
    session: AsyncSession, filters: ResolvedFilters
) -> list[Anomaly]:
    """Net pay moving more than SALARY_JUMP_PCT against the same employee's
    previous payslip.

    Compares two PERSISTED payslips. It does not recompute either, and it does
    not read a contract — a wage change is one possible cause of a jump, not
    the definition of one.
    """
    rows = (
        await session.execute(
            select(Payslip, Payrun, Employee)
            .join(Payrun, Payslip.payrun_id == Payrun.id)
            .join(Employee, Payslip.employee_id == Employee.id)
            .where(
                Payslip.deleted_at.is_(None),
                Payrun.deleted_at.is_(None),
                *_period_overlaps_payrun(filters),
                *_employee_conditions(filters),
            )
            .order_by(Payrun.period_end.desc())
        )
    ).all()

    found: list[Anomaly] = []
    for payslip, run, employee in rows:
        previous = (
            await session.execute(
                select(Payslip, Payrun)
                .join(Payrun, Payslip.payrun_id == Payrun.id)
                .where(
                    Payslip.employee_id == payslip.employee_id,
                    Payslip.id != payslip.id,
                    Payslip.deleted_at.is_(None),
                    Payrun.deleted_at.is_(None),
                    Payrun.period_end < run.period_start,
                )
                .order_by(Payrun.period_end.desc())
                .limit(1)
            )
        ).first()
        if previous is None:
            continue
        earlier, earlier_run = previous
        change = _pct_change(earlier.net_amount, payslip.net_amount)
        if change is None or abs(change) < SALARY_JUMP_PCT:
            continue
        direction = "increased" if change > 0 else "decreased"
        found.append(
            Anomaly(
                type="large_salary_jump",
                severity=SEVERITY_HIGH if abs(change) >= SALARY_JUMP_PCT * 2 else SEVERITY_MEDIUM,
                message=(
                    f"{employee.full_name}'s net pay {direction} by {abs(change)}% "
                    f"({earlier.net_amount} to {payslip.net_amount})."
                ),
                reason=(
                    f"Net pay moved {change}% against the previous payslip "
                    f"({earlier_run.period_start} to {earlier_run.period_end}). The threshold "
                    f"for flagging is {SALARY_JUMP_PCT}%, which is a configured convention, "
                    "not a payroll rule."
                ),
                employee_id=employee.public_id,
                employee_name=employee.full_name,
                department=employee.department.name if employee.department else None,
                period=f"{run.period_start}..{run.period_end}",
                current_value=str(payslip.net_amount),
                baseline=str(earlier.net_amount),
                navigate_to=f"/payroll/{run.public_id}",
                references=(payslip.public_id, earlier.public_id),
            )
        )
    return found


async def detect_unusual_overtime(
    session: AsyncSession, filters: ResolvedFilters
) -> list[Anomaly]:
    """Single attendance records longer than OVERTIME_HOURS.

    `worked_hours` is server-computed by `AttendanceService._compute`; this
    reads it rather than recalculating from check-in/check-out, so the anomaly
    and the Attendance screen can never disagree about how long a day was.
    """
    rows = (
        await session.execute(
            select(Attendance, Employee)
            .join(Employee, Attendance.employee_id == Employee.id)
            .where(
                Attendance.deleted_at.is_(None),
                Attendance.worked_hours.is_not(None),
                Attendance.worked_hours > OVERTIME_HOURS,
                func.date(Attendance.check_in) >= filters.period_start,
                func.date(Attendance.check_in) <= filters.period_end,
                *_employee_conditions(filters),
            )
            .order_by(Attendance.worked_hours.desc())
            .limit(50)
        )
    ).all()

    return [
        Anomaly(
            type="unusual_overtime",
            severity=SEVERITY_MEDIUM,
            message=(
                f"{employee.full_name} recorded {attendance.worked_hours} hours on "
                f"{attendance.check_in.date()}."
            ),
            reason=(
                f"A single attendance record exceeded {OVERTIME_HOURS} hours. Overtime is not "
                "itself an error; this flags it for review, and it does not affect pay unless a "
                "salary rule consumes it."
            ),
            employee_id=employee.public_id,
            employee_name=employee.full_name,
            department=employee.department.name if employee.department else None,
            period=attendance.check_in.date().isoformat(),
            current_value=str(attendance.worked_hours),
            baseline=str(OVERTIME_HOURS),
            navigate_to="/attendance",
            references=(attendance.public_id,),
        )
        for attendance, employee in rows
    ]


async def detect_missing_bank_details(
    session: AsyncSession, filters: ResolvedFilters
) -> list[Anomaly]:
    """Active employees with no bank account.

    Severity is HIGH because this is one of the findings that BLOCKS payroll
    validation once the employee is in a run — but this detector deliberately
    looks at every active employee, not only those already on a payslip, so it
    can be fixed before a payrun is built rather than after Compute rejects it.
    """
    rows = (
        (
            await session.execute(
                select(Employee).where(
                    Employee.bank_account.is_(None),
                    Employee.status == EmployeeStatus.ACTIVE,
                    *_employee_conditions(filters, require_active=True),
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        Anomaly(
            type="missing_bank_details",
            severity=SEVERITY_HIGH,
            message=f"{employee.full_name} has no bank account on file.",
            reason=(
                "An active employee with no bank account cannot be paid, and this becomes a "
                "BLOCKING payroll finding once they are included in a payrun. Fixing the record "
                "is not enough on its own — the payrun must then be recomputed."
            ),
            employee_id=employee.public_id,
            employee_name=employee.full_name,
            department=employee.department.name if employee.department else None,
            navigate_to=f"/employees/{employee.public_id}",
        )
        for employee in rows
    ]


async def detect_missing_checkout(
    session: AsyncSession, filters: ResolvedFilters
) -> list[Anomaly]:
    """Attendance records the engine derived as `missing_checkout`.

    Reads the persisted status rather than re-deriving it from a NULL
    `check_out`: `derive_attendance_status` only calls a day missing once that
    day has ended, and a naive "check_out IS NULL" would flag everyone who is
    currently at work.
    """
    rows = (
        await session.execute(
            select(Attendance, Employee)
            .join(Employee, Attendance.employee_id == Employee.id)
            .where(
                Attendance.deleted_at.is_(None),
                Attendance.status == AttendanceStatus.MISSING_CHECKOUT,
                func.date(Attendance.check_in) >= filters.period_start,
                func.date(Attendance.check_in) <= filters.period_end,
                *_employee_conditions(filters),
            )
            .order_by(Attendance.check_in.desc())
            .limit(50)
        )
    ).all()

    return [
        Anomaly(
            type="missing_checkout",
            severity=SEVERITY_HIGH,
            message=(
                f"{employee.full_name} never checked out on {attendance.check_in.date()}."
            ),
            reason=(
                "A stored missing_checkout finding is BLOCKING and prevents payroll validation. "
                "Correct the attendance record, then recompute the payrun — correcting alone "
                "does not clear the finding already on the payslip."
            ),
            employee_id=employee.public_id,
            employee_name=employee.full_name,
            department=employee.department.name if employee.department else None,
            period=attendance.check_in.date().isoformat(),
            navigate_to="/attendance",
            references=(attendance.public_id,),
        )
        for attendance, employee in rows
    ]


async def detect_low_attendance(
    session: AsyncSession, filters: ResolvedFilters, *, today: Optional[date] = None
) -> list[Anomaly]:
    """Employees whose recorded days fall below LOW_ATTENDANCE_PCT of their
    scheduled working days SO FAR.

    Scheduled days come from `schedule_expectations` — the same function the
    payroll context and the Attendance screen use — so "expected" here means
    the same thing it means everywhere else. An employee with no usable
    schedule is SKIPPED rather than reported as 0%: an unknown denominator is
    not evidence of absence, and inventing one is how the LOP bug would have
    been reintroduced by the back door.

    THE DENOMINATOR STOPS AT TODAY, and that is the difference between a useful
    check and a permanently-red panel. The anomaly screen defaults to the
    current month, so counting the whole month would mean that on the 3rd
    everyone has "attended 2 of 22 days" and every employee in the company is
    flagged — every month, until the month ends. Days that have not happened
    are not absences. A period entirely in the future has no elapsed scheduled
    days at all and is skipped rather than reported as total absence.
    """
    from app.services.attendance import schedule_expectations
    from app.services.payroll_context import period_days

    today = today or date.today()
    employees = (
        (
            await session.execute(
                select(Employee).where(*_employee_conditions(filters, require_active=True))
            )
        )
        .scalars()
        .all()
    )

    # Only the part of the period that has actually elapsed.
    horizon = min(filters.period_end, today)
    elapsed_days = period_days(filters.period_start, horizon)

    found: list[Anomaly] = []
    for employee in employees:
        scheduled = 0
        for day in elapsed_days:
            _, net_hours = schedule_expectations(employee, day)
            if net_hours is not None and net_hours > 0:
                scheduled += 1
        if scheduled == 0:
            continue

        attended = (
            await session.execute(
                select(func.count(func.distinct(func.date(Attendance.check_in)))).where(
                    Attendance.employee_id == employee.id,
                    Attendance.deleted_at.is_(None),
                    func.date(Attendance.check_in) >= filters.period_start,
                    # The same horizon as the denominator. Counting attendance
                    # past `today` against scheduled days up to `today` would
                    # make the ratio exceed 100% for a backdated record.
                    func.date(Attendance.check_in) <= horizon,
                )
            )
        ).scalar() or 0

        pct = (Decimal(attended) / Decimal(scheduled) * Decimal("100")).quantize(Decimal("0.01"))
        if pct >= LOW_ATTENDANCE_PCT:
            continue
        found.append(
            Anomaly(
                type="low_attendance",
                severity=SEVERITY_MEDIUM,
                message=(
                    f"{employee.full_name} attended {attended} of {scheduled} scheduled days "
                    f"so far ({pct}%)."
                ),
                reason=(
                    f"Recorded attendance is below the {LOW_ATTENDANCE_PCT}% convention for the "
                    f"elapsed part of this period ({filters.period_start} to {horizon}). Approved "
                    "leave is NOT excluded from this count, so an employee on authorised absence "
                    "appears here legitimately — check their time off before treating it as a "
                    "problem."
                ),
                employee_id=employee.public_id,
                employee_name=employee.full_name,
                department=employee.department.name if employee.department else None,
                period=f"{filters.period_start}..{horizon}",
                current_value=f"{pct}",
                baseline=f"{LOW_ATTENDANCE_PCT}",
                navigate_to=f"/employees/{employee.public_id}",
            )
        )
    return found


async def detect_contract_expiring(
    session: AsyncSession, filters: ResolvedFilters, *, today: Optional[date] = None
) -> list[Anomaly]:
    """Active contracts ending within CONTRACT_EXPIRY_DAYS."""
    today = today or date.today()
    horizon = today + timedelta(days=CONTRACT_EXPIRY_DAYS)
    rows = (
        (
            await session.execute(
                select(Contract)
                .join(Employee, Contract.employee_id == Employee.id)
                .where(
                    Contract.deleted_at.is_(None),
                    Contract.status == ContractStatus.ACTIVE,
                    Contract.end_date.is_not(None),
                    Contract.end_date >= today,
                    Contract.end_date <= horizon,
                    *_employee_conditions(filters),
                )
                .order_by(Contract.end_date)
            )
        )
        .scalars()
        .all()
    )
    return [
        Anomaly(
            type="contract_expiring",
            severity=SEVERITY_MEDIUM,
            message=(
                f"{contract.employee.full_name}'s contract ends on {contract.end_date} "
                f"({(contract.end_date - today).days} days)."
            ),
            reason=(
                f"Active contract ending within {CONTRACT_EXPIRY_DAYS} days. A payroll period "
                "after that date will have no applicable contract and will not compute for "
                "this employee."
            ),
            employee_id=contract.employee.public_id,
            employee_name=contract.employee.full_name,
            department=contract.employee.department.name if contract.employee.department else None,
            current_value=contract.end_date.isoformat(),
            baseline=today.isoformat(),
            navigate_to="/contracts",
            references=(contract.public_id,),
        )
        for contract in rows
    ]


async def detect_department_spend_spike(
    session: AsyncSession, filters: ResolvedFilters
) -> list[Anomaly]:
    """A department's total net pay moving more than DEPARTMENT_SPIKE_PCT
    against the previous calendar month.

    Both totals come from `DashboardService._department_salary_totals`, so this
    detector and the Reports screen cannot report different department costs
    for the same period.
    """
    from app.ai.context_builder import _previous_month
    from app.services.dashboard import DashboardService

    service = DashboardService(session)
    previous_start, previous_end = _previous_month(filters.period_start)
    previous_filters = ResolvedFilters(
        period_start=previous_start,
        period_end=previous_end,
        department_id=filters.department_id,
        department_public_id=filters.department_public_id,
        employee_type=filters.employee_type,
    )

    current = await service._department_salary_totals(filters)
    earlier = await service._department_salary_totals(previous_filters)

    # Department totals count only PAID payslips. A period whose payroll has
    # not been run or finalized yet therefore has a total of zero for EVERY
    # department, and comparing it against a paid month would report "fell
    # 100%" for all of them — four confident findings describing nothing but
    # "it is the 5th of the month".
    #
    # So: if nothing at all was paid in the current period, there is no spend
    # to compare and the detector stays silent. If SOME departments were paid
    # and one was not, that one's 100% fall is genuine and is reported.
    if not any(total > 0 for total in current.values()):
        return []

    names = await service._department_names(
        {key for key in set(current) | set(earlier) if key is not None}
    )

    found: list[Anomaly] = []
    for dept_id in set(current) | set(earlier):
        now_total = current.get(dept_id, Decimal("0.00"))
        was_total = earlier.get(dept_id, Decimal("0.00"))
        change = _pct_change(was_total, now_total)
        if change is None or abs(change) < DEPARTMENT_SPIKE_PCT:
            continue
        public_id, name = names.get(dept_id, (None, "Unassigned"))
        direction = "rose" if change > 0 else "fell"
        found.append(
            Anomaly(
                type="department_spend_spike",
                severity=SEVERITY_MEDIUM,
                message=f"{name} payroll {direction} {abs(change)}% ({was_total} to {now_total}).",
                reason=(
                    f"Department net pay moved {change}% against "
                    f"{previous_start}..{previous_end}. The threshold is "
                    f"{DEPARTMENT_SPIKE_PCT}%, a configured convention. A movement here has "
                    "many legitimate causes — a joiner, a leaver, unpaid leave — and this does "
                    "not assert which."
                ),
                department=name,
                period=f"{filters.period_start}..{filters.period_end}",
                current_value=str(now_total),
                baseline=str(was_total),
                navigate_to="/reports",
                references=(public_id,) if public_id else (),
            )
        )
    return found


#: Registered detectors, in the order PRD §5.7 lists them.
DETECTORS = (
    detect_large_salary_jump,
    detect_unusual_overtime,
    detect_missing_bank_details,
    detect_missing_checkout,
    detect_low_attendance,
    detect_contract_expiring,
    detect_department_spend_spike,
)

_SEVERITY_ORDER = {SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 1, SEVERITY_LOW: 2}


async def detect_all(session: AsyncSession, filters: ResolvedFilters) -> list[dict]:
    """Run every detector and return the findings, most severe first.

    A detector that raises does NOT take the others down with it: the anomaly
    panel exists to surface problems, and one broken check silently hiding six
    working ones would be the worst possible failure mode for it. The failure
    is reported as a finding of its own so it is visible rather than absent.
    """
    findings: list[dict] = []
    for detector in DETECTORS:
        try:
            for anomaly in await detector(session, filters):
                findings.append(anomaly.as_dict())
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            findings.append(
                Anomaly(
                    type="detector_failed",
                    severity=SEVERITY_LOW,
                    message=f"The '{detector.__name__}' check could not run.",
                    reason=(
                        f"{type(exc).__name__}: {exc}. Other checks ran normally; this one's "
                        "findings are missing rather than empty."
                    ),
                ).as_dict()
            )
    findings.sort(key=lambda row: _SEVERITY_ORDER.get(row["severity"], 3))
    return findings


async def summarize(findings: list[dict]) -> dict:
    by_severity: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for row in findings:
        by_severity[row["severity"]] = by_severity.get(row["severity"], 0) + 1
        by_type[row["type"]] = by_type.get(row["type"], 0) + 1
    return {"total": len(findings), "by_severity": by_severity, "by_type": by_type}


__all__ = [
    "Anomaly",
    "CONTRACT_EXPIRY_DAYS",
    "DEPARTMENT_SPIKE_PCT",
    "DETECTORS",
    "LOW_ATTENDANCE_PCT",
    "OVERTIME_HOURS",
    "SALARY_JUMP_PCT",
    "detect_all",
    "summarize",
]
