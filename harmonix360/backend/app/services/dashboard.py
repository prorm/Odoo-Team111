"""Payroll dashboard — read-only analytics (PS A7 / B9).

Every function here is a pure read. Nothing in this module ever calls
`session.flush()`, `session.commit()`, or touches a repository's write path —
the dashboard cannot mutate anything it reports on. See
`docs/dashboard-data-contract.md` for what each function returns and why;
this module is the implementation of that contract, not a second copy of the
definitions.

Payroll figures (`total_net_salary_paid`, `average_salary`,
`salary_cost_by_department`, `monthly_net_salary_trend`, `payroll_warnings`)
read `Payslip.net_amount`/`.warnings` directly — the rule engine's own
authoritative output (Architecture §7/§10) — and never recompute a total from
wage/allowances/deductions. `Payslip.net_amount` already exists as a real
column; only Phase 4's compute engine, which writes it, is still pending. See
`docs/dashboard-phase4-integration.md`.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.attendance import Attendance
from app.models.contract import Contract
from app.models.department import Department
from app.models.employee import Employee
from app.models.enums import (
    WEEKDAY_ORDER,
    AttendanceStatus,
    ContractStatus,
    EmployeeStatus,
    EmployeeType,
    PayslipStatus,
    TimeOffAllocationStatus,
    TimeOffRequestStatus,
)
from app.models.payroll import Payrun, Payslip
from app.models.time_off import TimeOffAllocation, TimeOffRequest, TimeOffType
from app.models.working_schedule import WorkingSchedule
from app.repositories.hr import DepartmentRepository

TWO_PLACES = Decimal("0.01")

#: Horizon for the "contract approaching expiration" alert (task §I). A
#: product choice, not a schema fact — change here if a different window is
#: wanted.
CONTRACT_EXPIRY_HORIZON_DAYS = 30

#: Categories `_normalize_warning` recognizes (PRD §5.7, task §H). Anything
#: else — including a bare string, or a dict with none of these keys — is
#: reported as "other" rather than dropped or invented.
_RECOGNIZED_WARNING_CATEGORIES = frozenset(
    {"missing_bank_details", "duplicate_payslip", "missing_contract", "contract_attention"}
)

#: Weekday.MONDAY..SUNDAY declares in the same order as Python's
#: date.weekday() (Monday=0..Sunday=6) — WEEKDAY_ORDER already encodes that,
#: so inverting it gives a direct index -> Weekday lookup.
_INDEX_TO_WEEKDAY = {index: day for day, index in WEEKDAY_ORDER.items()}


@dataclass(frozen=True)
class ResolvedFilters:
    """The dashboard's filter set, with `department_id` already resolved to
    an internal integer id (or None) so every query below can compare
    directly against `Employee.department_id` without re-decoding a hashid
    per call."""

    period_start: date
    period_end: date
    department_id: Optional[int]
    department_public_id: Optional[str]
    employee_type: Optional[EmployeeType]


def _default_period(today: Optional[date] = None) -> tuple[date, date]:
    """The current calendar month, used when no period is supplied."""
    today = today or date.today()
    start = today.replace(day=1)
    last_day = calendar.monthrange(today.year, today.month)[1]
    return start, today.replace(day=last_day)


async def resolve_filters(
    session: AsyncSession,
    *,
    period_start: Optional[date],
    period_end: Optional[date],
    department_id: Optional[str],
    employee_type: Optional[EmployeeType],
) -> ResolvedFilters:
    if period_start is None and period_end is None:
        period_start, period_end = _default_period()
    elif period_start is None or period_end is None:
        raise HTTPException(400, "period_start and period_end must both be provided, or both omitted")

    if period_start > period_end:
        raise HTTPException(400, "period_start must not be after period_end")

    dept_internal_id = None
    if department_id:
        department = await DepartmentRepository(session).get_by_public_id(department_id)
        if department is None:
            raise HTTPException(404, f"Department '{department_id}' not found")
        dept_internal_id = department.id

    return ResolvedFilters(
        period_start=period_start,
        period_end=period_end,
        department_id=dept_internal_id,
        department_public_id=department_id,
        employee_type=employee_type,
    )


def _employee_conditions(filters: ResolvedFilters, *, require_active: bool = False) -> list:
    conditions: list = [Employee.deleted_at.is_(None)]
    if require_active:
        conditions.append(Employee.status == EmployeeStatus.ACTIVE)
    if filters.department_id is not None:
        conditions.append(Employee.department_id == filters.department_id)
    if filters.employee_type is not None:
        conditions.append(Employee.employee_type == filters.employee_type)
    return conditions


def _period_overlaps_payrun(filters: ResolvedFilters):
    return (Payrun.period_start <= filters.period_end, Payrun.period_end >= filters.period_start)


def _expected_working_days_for_schedule(
    schedule: Optional[WorkingSchedule], period_start: date, period_end: date
) -> int:
    if schedule is None or schedule.deleted_at is not None:
        return 0
    active_weekdays = {line.day_of_week for line in schedule.lines}
    if not active_weekdays:
        return 0
    total = 0
    current = period_start
    one_day = timedelta(days=1)
    while current <= period_end:
        if _INDEX_TO_WEEKDAY[current.weekday()] in active_weekdays:
            total += 1
        current += one_day
    return total


def _normalize_warning(raw: Any) -> tuple[str, str]:
    if isinstance(raw, dict):
        category = str(raw.get("category") or raw.get("type") or raw.get("code") or "other").strip().lower()
        message = str(raw.get("message") or raw.get("detail") or raw)
        if category not in _RECOGNIZED_WARNING_CATEGORIES:
            category = "other"
        return category, message
    return "other", str(raw)


class DashboardService:
    """Read-only. See the module docstring — no method here ever writes."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # ------------------------------------------------------------- KPIs (§4)

    async def total_net_salary_paid(self, filters: ResolvedFilters) -> Decimal:
        stmt = (
            select(func.coalesce(func.sum(Payslip.net_amount), Decimal("0.00")))
            .select_from(Payslip)
            .join(Payrun, Payslip.payrun_id == Payrun.id)
            .join(Employee, Payslip.employee_id == Employee.id)
            .where(
                Payslip.deleted_at.is_(None),
                Payslip.status == PayslipStatus.PAID,
                *_period_overlaps_payrun(filters),
                *_employee_conditions(filters),
            )
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def payslips_generated(self, filters: ResolvedFilters) -> int:
        stmt = (
            select(func.count(Payslip.id))
            .select_from(Payslip)
            .join(Payrun, Payslip.payrun_id == Payrun.id)
            .join(Employee, Payslip.employee_id == Employee.id)
            .where(
                Payslip.deleted_at.is_(None),
                Payslip.status != PayslipStatus.CANCELLED,
                *_period_overlaps_payrun(filters),
                *_employee_conditions(filters),
            )
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def average_salary(self, filters: ResolvedFilters) -> Decimal:
        """Average PAID net total per employee — see data contract §4.C for
        why this groups by employee before averaging, rather than dividing
        the flat total by the payslip count."""
        per_employee = (
            select(Payslip.employee_id, func.sum(Payslip.net_amount).label("total"))
            .join(Payrun, Payslip.payrun_id == Payrun.id)
            .join(Employee, Payslip.employee_id == Employee.id)
            .where(
                Payslip.deleted_at.is_(None),
                Payslip.status == PayslipStatus.PAID,
                *_period_overlaps_payrun(filters),
                *_employee_conditions(filters),
            )
            .group_by(Payslip.employee_id)
            .subquery()
        )
        result = (await self.session.execute(select(func.avg(per_employee.c.total)))).scalar()
        if result is None:
            return Decimal("0.00")
        return Decimal(str(result)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    async def approved_time_off(self, filters: ResolvedFilters) -> int:
        stmt = (
            select(func.count(TimeOffRequest.id))
            .join(Employee, TimeOffRequest.employee_id == Employee.id)
            .where(
                TimeOffRequest.deleted_at.is_(None),
                TimeOffRequest.status == TimeOffRequestStatus.APPROVED,
                TimeOffRequest.date_from <= filters.period_end,
                TimeOffRequest.date_to >= filters.period_start,
                *_employee_conditions(filters),
            )
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def attendance_health(self, filters: ResolvedFilters) -> tuple[int, int]:
        """`(expected_working_days, attended_days)`, over the SAME set of
        active, filter-matching employees for both numbers — see data
        contract §4.E. Never `present_count / 30`: `expected_working_days`
        comes from each employee's actual `WorkingSchedule`/`ScheduleLine`
        pattern."""
        stmt = (
            select(Employee)
            .where(*_employee_conditions(filters, require_active=True))
            .options(selectinload(Employee.default_schedule).selectinload(WorkingSchedule.lines))
        )
        employees = list((await self.session.execute(stmt)).scalars().all())

        expected = sum(
            _expected_working_days_for_schedule(e.default_schedule, filters.period_start, filters.period_end)
            for e in employees
        )

        employee_ids = [e.id for e in employees]
        if not employee_ids:
            return expected, 0

        attended_stmt = select(func.count(Attendance.id)).where(
            Attendance.deleted_at.is_(None),
            Attendance.employee_id.in_(employee_ids),
            Attendance.status != AttendanceStatus.ABSENT,
            func.date(Attendance.check_in) >= filters.period_start,
            func.date(Attendance.check_in) <= filters.period_end,
        )
        attended = (await self.session.execute(attended_stmt)).scalar_one()
        return expected, attended

    @staticmethod
    def attendance_health_pct(expected_working_days: int, attended_days: int) -> Optional[Decimal]:
        if expected_working_days <= 0:
            return None
        pct = (Decimal(attended_days) / Decimal(expected_working_days)) * Decimal(100)
        return pct.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    # ----------------------------------------------------------- charts (§5)

    async def _department_salary_totals(self, filters: ResolvedFilters) -> dict[Optional[int], Decimal]:
        stmt = (
            select(Employee.department_id, func.coalesce(func.sum(Payslip.net_amount), Decimal("0.00")))
            .select_from(Payslip)
            .join(Payrun, Payslip.payrun_id == Payrun.id)
            .join(Employee, Payslip.employee_id == Employee.id)
            .where(
                Payslip.deleted_at.is_(None),
                Payslip.status == PayslipStatus.PAID,
                *_period_overlaps_payrun(filters),
                *_employee_conditions(filters),
            )
            .group_by(Employee.department_id)
        )
        return dict((await self.session.execute(stmt)).all())

    async def _department_names(self, department_ids: set[int]) -> dict[int, tuple[str, str]]:
        if not department_ids:
            return {}
        rows = (
            await self.session.execute(
                select(Department.id, Department.public_id, Department.name).where(
                    Department.id.in_(department_ids)
                )
            )
        ).all()
        return {row.id: (row.public_id, row.name) for row in rows}

    async def salary_cost_by_department(self, filters: ResolvedFilters) -> list[dict]:
        totals = await self._department_salary_totals(filters)
        names = await self._department_names({k for k in totals if k is not None})

        out = []
        for dept_id, amount in totals.items():
            if dept_id is None:
                out.append({"department_id": None, "department": "Unassigned", "amount": amount})
            else:
                public_id, name = names.get(dept_id, (None, "Unknown"))
                out.append({"department_id": public_id, "department": name, "amount": amount})
        out.sort(key=lambda row: row["department"])
        return out

    async def monthly_net_salary_trend(self, filters: ResolvedFilters) -> list[dict]:
        month_expr = func.to_char(Payrun.period_start, "YYYY-MM").label("month")
        stmt = (
            select(month_expr, func.coalesce(func.sum(Payslip.net_amount), Decimal("0.00")).label("amount"))
            .select_from(Payslip)
            .join(Payrun, Payslip.payrun_id == Payrun.id)
            .join(Employee, Payslip.employee_id == Employee.id)
            .where(
                Payslip.deleted_at.is_(None),
                Payslip.status == PayslipStatus.PAID,
                *_period_overlaps_payrun(filters),
                *_employee_conditions(filters),
            )
            .group_by(month_expr)
            .order_by(month_expr)
        )
        rows = (await self.session.execute(stmt)).all()
        return [{"month": row.month, "amount": row.amount} for row in rows]

    # -------------------------------------------------------- alerts (§6)

    async def payroll_warnings(self, filters: ResolvedFilters) -> list[dict]:
        stmt = (
            select(Payslip, Employee)
            .join(Payrun, Payslip.payrun_id == Payrun.id)
            .join(Employee, Payslip.employee_id == Employee.id)
            .where(
                Payslip.deleted_at.is_(None),
                Payslip.warnings.isnot(None),
                *_period_overlaps_payrun(filters),
                *_employee_conditions(filters),
            )
        )
        rows = (await self.session.execute(stmt)).all()
        out = []
        for payslip, employee in rows:
            for raw in payslip.warnings or []:
                category, message = _normalize_warning(raw)
                out.append(
                    {
                        "category": category,
                        "message": message,
                        "payslip_id": payslip.public_id,
                        "employee_id": employee.public_id,
                        "employee_name": employee.full_name,
                    }
                )
        return out

    async def contract_attention(self, filters: ResolvedFilters) -> list[dict]:
        today = date.today()
        horizon = today + timedelta(days=CONTRACT_EXPIRY_HORIZON_DAYS)
        out: list[dict] = []

        expiring_stmt = (
            select(Contract, Employee)
            .join(Employee, Contract.employee_id == Employee.id)
            .where(
                Contract.deleted_at.is_(None),
                Contract.status == ContractStatus.ACTIVE,
                Contract.end_date.isnot(None),
                Contract.end_date >= today,
                Contract.end_date <= horizon,
                *_employee_conditions(filters),
            )
            .order_by(Contract.end_date)
        )
        for contract, employee in (await self.session.execute(expiring_stmt)).all():
            out.append(
                {
                    "kind": "expiring",
                    "employee_id": employee.public_id,
                    "employee_name": employee.full_name,
                    "detail": f"Contract ends {contract.end_date.isoformat()}",
                }
            )

        active_contract_employee_ids = (
            select(Contract.employee_id)
            .where(
                Contract.deleted_at.is_(None),
                Contract.status == ContractStatus.ACTIVE,
                Contract.start_date <= today,
                or_(Contract.end_date.is_(None), Contract.end_date >= today),
            )
            .scalar_subquery()
        )
        missing_stmt = select(Employee).where(
            Employee.id.not_in(active_contract_employee_ids),
            *_employee_conditions(filters, require_active=True),
        )
        for employee in (await self.session.execute(missing_stmt)).scalars().all():
            out.append(
                {
                    "kind": "missing_contract",
                    "employee_id": employee.public_id,
                    "employee_name": employee.full_name,
                    "detail": "No active contract covers today",
                }
            )
        return out

    # ----------------------------------------------------- overviews (§7)

    async def attendance_by_status(self, filters: ResolvedFilters) -> list[dict]:
        stmt = (
            select(Attendance.status, func.count(Attendance.id))
            .join(Employee, Attendance.employee_id == Employee.id)
            .where(
                Attendance.deleted_at.is_(None),
                func.date(Attendance.check_in) >= filters.period_start,
                func.date(Attendance.check_in) <= filters.period_end,
                *_employee_conditions(filters),
            )
            .group_by(Attendance.status)
        )
        rows = dict((await self.session.execute(stmt)).all())
        return [{"status": status, "count": rows.get(status, 0)} for status in AttendanceStatus]

    async def time_off_overview(self, filters: ResolvedFilters) -> dict:
        status_stmt = (
            select(TimeOffRequest.status, func.count(TimeOffRequest.id))
            .join(Employee, TimeOffRequest.employee_id == Employee.id)
            .where(
                TimeOffRequest.deleted_at.is_(None),
                TimeOffRequest.date_from <= filters.period_end,
                TimeOffRequest.date_to >= filters.period_start,
                *_employee_conditions(filters),
            )
            .group_by(TimeOffRequest.status)
        )
        counts = dict((await self.session.execute(status_stmt)).all())

        today = date.today()
        balance_stmt = (
            select(
                TimeOffType.id,
                TimeOffType.public_id,
                TimeOffType.name,
                func.coalesce(func.sum(TimeOffAllocation.allocated), Decimal("0.00")),
                func.coalesce(func.sum(TimeOffAllocation.taken), Decimal("0.00")),
            )
            .select_from(TimeOffAllocation)
            .join(TimeOffType, TimeOffAllocation.time_off_type_id == TimeOffType.id)
            .join(Employee, TimeOffAllocation.employee_id == Employee.id)
            .where(
                TimeOffAllocation.deleted_at.is_(None),
                TimeOffAllocation.status == TimeOffAllocationStatus.CONFIRMED,
                TimeOffAllocation.valid_from <= today,
                or_(TimeOffAllocation.valid_to.is_(None), TimeOffAllocation.valid_to >= today),
                *_employee_conditions(filters),
            )
            .group_by(TimeOffType.id, TimeOffType.public_id, TimeOffType.name)
        )
        balance_summary = [
            {
                "time_off_type_id": type_public_id,
                "time_off_type": name,
                "allocated": allocated,
                "taken": taken,
                "remaining": allocated - taken,
            }
            for _id, type_public_id, name, allocated, taken in (
                await self.session.execute(balance_stmt)
            ).all()
        ]

        return {
            "pending": counts.get(TimeOffRequestStatus.TO_APPROVE, 0),
            "approved": counts.get(TimeOffRequestStatus.APPROVED, 0),
            "refused": counts.get(TimeOffRequestStatus.REFUSED, 0),
            "balance_summary": balance_summary,
        }

    async def department_breakdown(self, filters: ResolvedFilters) -> list[dict]:
        salary_totals = await self._department_salary_totals(filters)

        headcount_stmt = (
            select(Employee.department_id, func.count(Employee.id))
            .where(*_employee_conditions(filters, require_active=True))
            .group_by(Employee.department_id)
        )
        headcounts = dict((await self.session.execute(headcount_stmt)).all())

        department_ids = {k for k in (set(headcounts) | set(salary_totals)) if k is not None}
        names = await self._department_names(department_ids)

        out = []
        for dept_id in set(headcounts) | set(salary_totals):
            if dept_id is None:
                public_id, name = None, "Unassigned"
            else:
                public_id, name = names.get(dept_id, (None, "Unknown"))
            out.append(
                {
                    "department_id": public_id,
                    "department": name,
                    "headcount": headcounts.get(dept_id, 0),
                    "payroll_spend": salary_totals.get(dept_id, Decimal("0.00")),
                }
            )
        out.sort(key=lambda row: row["department"])
        return out

    # --------------------------------------------------------- orchestration

    async def get_dashboard(self, filters: ResolvedFilters) -> dict:
        total_net_salary_paid = await self.total_net_salary_paid(filters)
        payslips_generated = await self.payslips_generated(filters)
        average_salary = await self.average_salary(filters)
        approved_time_off = await self.approved_time_off(filters)
        expected_working_days, attended_days = await self.attendance_health(filters)

        return {
            "filters": {
                "period_start": filters.period_start,
                "period_end": filters.period_end,
                "department_id": filters.department_public_id,
                "employee_type": filters.employee_type,
            },
            "kpis": {
                "total_net_salary_paid": total_net_salary_paid,
                "payslips_generated": payslips_generated,
                "average_salary": average_salary,
                "approved_time_off": approved_time_off,
                "attendance_health_pct": self.attendance_health_pct(expected_working_days, attended_days),
            },
            "salary_cost_by_department": await self.salary_cost_by_department(filters),
            "monthly_net_salary_trend": await self.monthly_net_salary_trend(filters),
            "attendance": {
                "by_status": await self.attendance_by_status(filters),
                "expected_working_days": expected_working_days,
                "attended_days": attended_days,
            },
            "time_off": await self.time_off_overview(filters),
            "warnings": await self.payroll_warnings(filters),
            "contract_attention": await self.contract_attention(filters),
            "department_breakdown": await self.department_breakdown(filters),
        }
