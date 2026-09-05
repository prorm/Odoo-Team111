/** Payroll dashboard types (PS A7 / B9). Mirror app/schemas/dashboard.py —
 *  see docs/dashboard-data-contract.md for what each field means.
 *
 *  Money is `string`, like every other Decimal field this API returns
 *  (Contract.wage, Payslip.net_amount) — never parsed into a JS number here,
 *  only formatted for display. */

import type { EmployeeType } from './hr';

export interface DashboardFilters {
  period_start: string;
  period_end: string;
  department_id: string | null;
  employee_type: EmployeeType | null;
}

export interface DashboardKPIs {
  total_net_salary_paid: string;
  payslips_generated: number;
  average_salary: string;
  approved_time_off: number;
  /** null when no matching employee has a working schedule — never a
   *  fabricated 0 or 100. */
  attendance_health_pct: string | null;
}

export interface DepartmentAmount {
  department_id: string | null;
  department: string;
  amount: string;
}

export interface MonthlyTrendPoint {
  month: string;
  amount: string;
}

export interface AttendanceStatusCount {
  status: string;
  count: number;
}

export interface AttendanceOverview {
  by_status: AttendanceStatusCount[];
  expected_working_days: number;
  attended_days: number;
}

export interface TimeOffBalanceSummary {
  time_off_type_id: string;
  time_off_type: string;
  allocated: string;
  taken: string;
  remaining: string;
}

export interface TimeOffOverview {
  pending: number;
  approved: number;
  refused: number;
  balance_summary: TimeOffBalanceSummary[];
}

/**
 * One warning the payroll engine wrote onto a payslip.
 *
 * `code` is Phase 4's own code string, unchanged end to end, so an alert here
 * and the warning on the payslip it came from are the same word — that is what
 * makes cross-referencing possible. `WARNING_LABELS` in ReportsPage.tsx turns
 * it into display text; it never becomes a different code.
 */
export interface PayrollWarning {
  code: string;
  /** 'blocking' stops the payrun being validated (PRD §5.10); 'advisory' does not. */
  severity: 'blocking' | 'advisory';
  message: string;
  /** Public ids of the records to open to fix this. */
  references: string[];
  /** False when the backend did not recognize the code — a warning added by a
   *  later phase. Still rendered, using the raw code. */
  recognized: boolean;
  payslip_id: string;
  employee_id: string;
  employee_name: string;
}

export interface ContractAttentionItem {
  kind: 'expiring' | 'missing_contract';
  employee_id: string;
  employee_name: string;
  detail: string;
}

export interface DepartmentBreakdownItem {
  department_id: string | null;
  department: string;
  headcount: number;
  payroll_spend: string;
}

export interface DashboardResponse {
  filters: DashboardFilters;
  kpis: DashboardKPIs;
  salary_cost_by_department: DepartmentAmount[];
  monthly_net_salary_trend: MonthlyTrendPoint[];
  attendance: AttendanceOverview;
  time_off: TimeOffOverview;
  warnings: PayrollWarning[];
  contract_attention: ContractAttentionItem[];
  department_breakdown: DepartmentBreakdownItem[];
}

export interface DashboardFilterParams {
  period_start?: string;
  period_end?: string;
  department_id?: string;
  employee_type?: EmployeeType;
}
