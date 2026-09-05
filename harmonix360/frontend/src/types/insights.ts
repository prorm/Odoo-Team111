/**
 * Phase 10 read-side wire types (PRD §5.6-§5.10).
 *
 * Every monetary field is `string`, exactly as the backend serialised it.
 * Nothing in this layer parses one into a `number` — JavaScript's `number` is
 * a double, `41800.00` comes back as `41800`, and a payslip that renders a
 * different figure than the one that was paid is the single worst bug this
 * product can ship. Amounts are rendered as received, and arithmetic on them
 * happens on the server or not at all.
 */

// -------------------------------------------------------- View Calculation

export interface CalculationInput {
  name: string;
  value: string;
  description: string;
}

export interface CalculationLine {
  code: string;
  name: string;
  category: string;
  sequence: number;
  amount: string;
  rule_still_exists: boolean;
  /** The rule AS IT IS TODAY — which may differ from what produced the line. */
  rule_definition_now?: {
    computation_method: string;
    expression: string | null;
    amount: string | null;
    percentage_base_code: string | null;
    is_active: boolean;
    current_name: string;
    current_category: string;
  };
  rule_renamed_since?: boolean;
  rule_recategorised_since?: boolean;
}

export interface CalculationTree {
  payslip_id: string;
  payrun: { payrun_id: string; name: string; period_start: string; period_end: string; status: string } | null;
  employee: Record<string, unknown> | null;
  contract: Record<string, unknown> | null;
  inputs: { available: boolean; reason: string | null; values: CalculationInput[] };
  lines: CalculationLine[];
  category_subtotals: { category: string; total: string }[];
  totals: { worked_days: string; gross_amount: string; net_amount: string };
  warnings: { code: string; severity: string; message: string; references: string[] }[];
  status: string;
  source: string;
}

// ------------------------------------------------------- Pay change (§5.9)

export interface Delta {
  before: string;
  after: string;
  delta: string | null;
  direction: 'increase' | 'decrease' | 'unchanged' | 'unknown';
}

export interface LineChange {
  code: string;
  name: string;
  category: string;
  change: 'changed' | 'added' | 'removed';
  before: string | null;
  after: string | null;
  delta: string | null;
  direction: 'increase' | 'decrease' | null;
}

export interface PayComparison {
  comparable: boolean;
  reason: string | null;
  current: Record<string, unknown>;
  previous: Record<string, unknown> | null;
  totals?: { worked_days: Delta | null; gross_amount: Delta | null; net_amount: Delta | null };
  input_changes?: Record<string, Delta>;
  line_changes?: LineChange[];
  contract_changed?: boolean;
  unavailable?: string[];
}

// ---------------------------------------------------- Time Machine (§5.8)

export interface TimelineContract {
  contract_id: string;
  wage: string;
  status: string;
  start_date: string;
  end_date: string | null;
  job_position: string | null;
  salary_structure: { code: string; name: string } | null;
  working_schedule: string | null;
  is_open_ended: boolean;
}

export interface WageChange {
  from_contract_id: string;
  to_contract_id: string;
  effective_date: string;
  wage_before: string;
  wage_after: string;
  wage_delta: string;
  direction: 'increase' | 'decrease' | 'unchanged';
  percent_change?: string;
  other_changed_fields: string[];
}

export interface ContractTimeline {
  employee_id: string;
  employee_name: string;
  contract_count: number;
  contracts: TimelineContract[];
  transitions: WageChange[];
  wage_changes: WageChange[];
  has_wage_change: boolean;
  resolution: {
    period_start: string;
    period_end: string;
    resolved: boolean;
    reason: string | null;
    contract: TimelineContract | null;
    covers_whole_period?: boolean;
  } | null;
}

// ------------------------------------------------------- Firewall (§5.10)

export interface FirewallIssue {
  message: string;
  severity: string;
  employee_id: string | null;
  employee_name: string | null;
  payslip_id: string | null;
  references: string[];
  navigate_to: string;
}

export interface FirewallGroup {
  code: string;
  title: string;
  fix: string;
  severity: string;
  /** False for a code with no guidance entry — shown, never hidden. */
  recognized: boolean;
  count: number;
  issues: FirewallIssue[];
}

export interface FirewallReport {
  payrun_id: string;
  payrun_name: string;
  period_start: string;
  period_end: string;
  status: string;
  version: number;
  blocking_count: number;
  advisory_count: number;
  blocking_by_code: Record<string, number>;
  can_validate: boolean;
  gate_message: string;
  groups: FirewallGroup[];
  revalidate: { method: string; path: string; requires_version: boolean };
}

// ------------------------------------------------------ Anomalies (§5.7)

export interface Anomaly {
  type: string;
  severity: 'high' | 'medium' | 'low' | string;
  message: string;
  reason: string;
  employee_id: string | null;
  employee_name: string | null;
  department: string | null;
  period: string | null;
  current_value: string | null;
  baseline: string | null;
  navigate_to: string | null;
  references: string[];
}

export interface AnomalyResponse {
  period_start: string;
  period_end: string;
  department_id: string | null;
  summary: { total: number; by_severity: Record<string, number>; by_type: Record<string, number> };
  anomalies: Anomaly[];
  thresholds: Record<string, string | number>;
}

/** Human labels for the seven deterministic checks. */
export const ANOMALY_LABELS: Record<string, string> = {
  large_salary_jump: 'Large salary change',
  unusual_overtime: 'Unusual overtime',
  missing_bank_details: 'Missing bank details',
  missing_checkout: 'Missing checkout',
  low_attendance: 'Low attendance',
  contract_expiring: 'Contract expiring',
  department_spend_spike: 'Department spend spike',
  detector_failed: 'A check could not run',
};

export const SEVERITY_VARIANT: Record<string, 'destructive' | 'warning' | 'secondary'> = {
  high: 'destructive',
  medium: 'warning',
  low: 'secondary',
};

// -------------------------------------------------------------- Realtime

export interface RealtimeEvent {
  channel: string;
  event: string;
  at?: string;
  data: Record<string, unknown>;
}

export type RealtimeState = 'connecting' | 'live' | 'unavailable';
