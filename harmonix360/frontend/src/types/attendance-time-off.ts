import type { EmployeeRef } from "./hr";
export interface Attendance {
  id: string;
  employee: EmployeeRef;
  check_in: string;
  check_out: string | null;
  worked_hours: string | null;
  status: string;
  version: number;
  correction_reason: string | null;
  corrected_by: string | null;
}
export interface TimeOffType {
  id: string;
  name: string;
  code: string;
  unit: "days" | "hours";
  requires_allocation: boolean;
  requires_approval: boolean;
  payroll_integration: boolean;
  description: string | null;
  version: number;
}
export interface TimeOffAllocation {
  id: string;
  employee: EmployeeRef;
  time_off_type: TimeOffType;
  allocated: string;
  taken: string;
  remaining: string;
  valid_from: string;
  valid_to: string | null;
  status: string;
  version: number;
}
export interface TimeOffRequest {
  id: string;
  employee: EmployeeRef;
  time_off_type: TimeOffType;
  date_from: string;
  date_to: string;
  duration: string;
  status: string;
  reason: string | null;
  decision_note: string | null;
  allocation_id: string | null;
  approved_by: string | null;
  version: number;
}
