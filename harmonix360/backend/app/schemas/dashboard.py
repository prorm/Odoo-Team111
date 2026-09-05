"""Payroll dashboard response shapes (PS A7 / B9).

See `docs/dashboard-data-contract.md` for the exact definition of every field
here — this module only carries the shapes, the contract doc carries the
meaning. Money is `Decimal`, exactly like every other monetary field in this
codebase (`ContractResponse.wage`); no float appears anywhere on this path
(Architecture §10).
"""
from datetime import date
from decimal import Decimal
from typing import List, Optional

from pydantic import Field

from app.models.enums import AttendanceStatus, EmployeeType
from app.schemas.common import ORMModel


class DashboardFilters(ORMModel):
    """Echoes back the effective filters — including server-applied defaults —
    so a client never has to guess what period an unfiltered request resolved
    to."""

    period_start: date
    period_end: date
    department_id: Optional[str] = None
    employee_type: Optional[EmployeeType] = None


class DashboardKPIs(ORMModel):
    #: SUM(Payslip.net_amount) where status='paid' — never reconstructed from
    #: wage/allowances/deductions. See data contract §4.A.
    total_net_salary_paid: Decimal
    #: COUNT(Payslip) where status != 'cancelled'. See §4.B.
    payslips_generated: int
    #: Average PER-EMPLOYEE paid net total for the period, not a flat
    #: total/count. See §4.C.
    average_salary: Decimal
    #: COUNT(TimeOffRequest) where status='approved'. See §4.D.
    approved_time_off: int
    #: attended_days / expected_working_days * 100, both derived from real
    #: WorkingSchedule/Attendance data. `None` when expected_working_days is 0
    #: (no matching employee has a schedule) rather than a fabricated 0/100.
    #: See §4.E.
    attendance_health_pct: Optional[Decimal] = None


class DepartmentAmount(ORMModel):
    department_id: Optional[str] = None
    department: str
    amount: Decimal


class MonthlyTrendPoint(ORMModel):
    #: "YYYY-MM", from Payrun.period_start.
    month: str
    amount: Decimal


class AttendanceStatusCount(ORMModel):
    status: AttendanceStatus
    count: int


class AttendanceOverview(ORMModel):
    by_status: List[AttendanceStatusCount]
    #: The two raw numbers behind `attendance_health_pct`, exposed so the UI
    #: can render "312 / 340 expected working days" rather than only a percent.
    expected_working_days: int
    attended_days: int


class TimeOffBalanceSummary(ORMModel):
    time_off_type_id: str
    time_off_type: str
    allocated: Decimal
    taken: Decimal
    remaining: Decimal


class TimeOffOverview(ORMModel):
    pending: int
    approved: int
    refused: int
    #: Snapshot as of today, not period-filtered — see data contract §7.K.
    balance_summary: List[TimeOffBalanceSummary]


class PayrollWarning(ORMModel):
    """One `Payslip.warnings` entry, as written by Phase 4's engine.

    `code`, `severity`, `message` and `references` are Phase 4's own four
    fields, passed through unchanged — `code` above all, so an alert here and
    the warning on the payslip it came from are the same string and can be
    cross-referenced. Display labels live in the frontend; this is data.
    """

    #: Phase 4's real warning code — `missing_bank_details`, `missing_checkout`,
    #: `contract_gap`, `duplicate_payslip`, `structure_mismatch`,
    #: `no_attendance` (and `no_payslip`, which Phase 4 derives at Validate).
    code: str
    #: "blocking" or "advisory". Blocking findings are what stop a payrun being
    #: validated (PRD §5.10); the distinction is the point of the field.
    severity: str
    message: str
    #: Public ids of the records to open to fix this — PRD §5.10's "navigation
    #: straight to the offending records".
    references: List[str] = Field(default_factory=list)
    #: False when this dashboard build does not know the code, i.e. Phase 4 (or
    #: a later phase) added a warning after this code was written. The entry is
    #: still reported in full rather than being relabelled or dropped.
    recognized: bool = True
    payslip_id: str
    employee_id: str
    employee_name: str


class ContractAttentionItem(ORMModel):
    kind: str = Field(description="'expiring' or 'missing_contract'")
    employee_id: str
    employee_name: str
    detail: str


class DepartmentBreakdownItem(ORMModel):
    department_id: Optional[str] = None
    department: str
    headcount: int
    payroll_spend: Decimal


class DashboardResponse(ORMModel):
    filters: DashboardFilters
    kpis: DashboardKPIs
    salary_cost_by_department: List[DepartmentAmount]
    monthly_net_salary_trend: List[MonthlyTrendPoint]
    attendance: AttendanceOverview
    time_off: TimeOffOverview
    warnings: List[PayrollWarning]
    contract_attention: List[ContractAttentionItem]
    department_breakdown: List[DepartmentBreakdownItem]
