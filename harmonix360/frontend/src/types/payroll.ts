import type { EmployeeRef } from './hr';

/**
 * Payrun / Payslip wire shapes (PS B5-B7).
 *
 * EVERY MONETARY FIELD IS A STRING, NOT A NUMBER. The backend serialises
 * `Decimal` as a JSON string precisely so it survives the wire exactly
 * (Architecture §10 forbids float anywhere on the payroll path), and
 * `JSON.parse` would turn a number back into an IEEE double — reintroducing,
 * in the browser, the imprecision the whole backend was built to avoid.
 *
 * So these stay strings all the way to the screen. Format them with
 * `formatMoney` (which never does arithmetic); if a total is ever needed
 * client-side, ask the server for it rather than adding these up in
 * JavaScript.
 */

export type PayrunStatus = 'draft' | 'computed' | 'validated' | 'paid' | 'cancelled';
export type PayslipStatus = PayrunStatus;

export type WarningSeverity = 'blocking' | 'advisory';

export interface SalaryStructureRef {
  id: string;
  name: string;
  code: string;
}

export interface PayrunRef {
  id: string;
  name: string;
  period_start: string;
  period_end: string;
  status: PayrunStatus;
}

export interface Payrun {
  id: string;
  name: string;
  period_start: string;
  period_end: string;
  status: PayrunStatus;
  notes: string | null;
  salary_structure: SalaryStructureRef;
  employees: EmployeeRef[];
  payslip_count: number;
  version: number;
}

export interface PayslipWarning {
  code: string;
  severity: WarningSeverity;
  message: string;
  references: string[];
}

export interface PayslipLine {
  id: string;
  code: string;
  name: string;
  category: 'basic' | 'allowance' | 'gross' | 'deduction' | 'net';
  sequence: number;
  /** Decimal-as-string. See the note at the top of this file. */
  amount: string;
}

export interface ContractRef {
  id: string;
  wage: string;
  start_date: string;
  end_date: string | null;
  job_position: string | null;
}

export interface Payslip {
  id: string;
  employee: EmployeeRef;
  contract: ContractRef;
  worked_days: string;
  gross_amount: string;
  net_amount: string;
  status: PayslipStatus;
  warnings: PayslipWarning[];
  lines: PayslipLine[];
  payrun: PayrunRef;
  version: number;
}

export interface SkippedEmployee {
  employee_id: string;
  employee_name: string;
  reason: string;
}

export interface ComputeResult {
  payrun: Payrun;
  computed_count: number;
  skipped: SkippedEmployee[];
  blocking_issues: Record<string, number>;
}

export interface ValidationIssue {
  code: string;
  severity: WarningSeverity;
  message: string;
  references: string[];
  payslip_id: string | null;
  employee_id: string | null;
}

export interface ValidationReport {
  payrun_id: string;
  status: PayrunStatus;
  blocking_count: number;
  advisory_count: number;
  blocking_by_code: Record<string, number>;
  issues: ValidationIssue[];
}

export interface EligibleEmployee {
  employee: EmployeeRef;
  contract_id: string;
  wage: string;
  partial_period: boolean;
}

export interface DeliveryResult {
  payrun_id: string;
  task_id: string;
  payslip_count: number;
  detail: string;
}

/** Human labels for the warning codes the engine emits (PRD §5.10's
 *  "categorized" issue list). Falls back to the raw code, so a warning added
 *  server-side still renders rather than disappearing. */
export const WARNING_LABELS: Record<string, string> = {
  missing_bank_details: 'Missing bank details',
  missing_checkout: 'Missing check-out',
  contract_gap: 'Contract does not cover the period',
  duplicate_payslip: 'Duplicate payslip for this period',
  structure_mismatch: 'Contract names a different salary structure',
  no_attendance: 'No attendance recorded',
  no_payslip: 'Selected, but no payslip could be computed',
};

/** Formats a Decimal-as-string for display WITHOUT parsing it into a float.
 *  Grouping is applied to the integer part by string manipulation only, so
 *  the value shown is byte-for-byte the value the server computed. */
export function formatMoney(value: string | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—';
  const negative = value.startsWith('-');
  const unsigned = negative ? value.slice(1) : value;
  const [whole, fraction = '00'] = unsigned.split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  return `${negative ? '-' : ''}${grouped}.${fraction}`;
}
