/** HR domain types. Mirror app/schemas/*.py — values must stay byte-identical
 *  to the backend enums, because they cross the wire verbatim.
 *
 *  Money and hours are `string`, not `number`, throughout. The backend sends
 *  them as decimal strings on purpose (Architecture §10): parsing a wage into a
 *  JS number makes it a float, and a float cannot represent 0.10 exactly. They
 *  are formatted for display and sent straight back on submit — never used in
 *  arithmetic here. Any total belongs on the server. */

export enum EmployeeStatus {
  ACTIVE = 'active',
  ON_LEAVE = 'on_leave',
  NOTICE_PERIOD = 'notice_period',
  EXITED = 'exited',
}

export enum EmployeeType {
  PERMANENT = 'permanent',
  CONTRACT = 'contract',
  INTERN = 'intern',
  PART_TIME = 'part_time',
}

export enum ContractStatus {
  DRAFT = 'draft',
  ACTIVE = 'active',
  EXPIRED = 'expired',
  CANCELLED = 'cancelled',
}

export enum WorkingScheduleType {
  FULL_TIME = 'full_time',
  PART_TIME = 'part_time',
  FLEXIBLE = 'flexible',
}

export enum Weekday {
  MONDAY = 'monday',
  TUESDAY = 'tuesday',
  WEDNESDAY = 'wednesday',
  THURSDAY = 'thursday',
  FRIDAY = 'friday',
  SATURDAY = 'saturday',
  SUNDAY = 'sunday',
}

export const WEEKDAY_ORDER: Weekday[] = [
  Weekday.MONDAY,
  Weekday.TUESDAY,
  Weekday.WEDNESDAY,
  Weekday.THURSDAY,
  Weekday.FRIDAY,
  Weekday.SATURDAY,
  Weekday.SUNDAY,
];

export const EMPLOYEE_STATUS_LABELS: Record<EmployeeStatus, string> = {
  [EmployeeStatus.ACTIVE]: 'Active',
  [EmployeeStatus.ON_LEAVE]: 'On leave',
  [EmployeeStatus.NOTICE_PERIOD]: 'Notice period',
  [EmployeeStatus.EXITED]: 'Exited',
};

export const EMPLOYEE_TYPE_LABELS: Record<EmployeeType, string> = {
  [EmployeeType.PERMANENT]: 'Permanent',
  [EmployeeType.CONTRACT]: 'Contract',
  [EmployeeType.INTERN]: 'Intern',
  [EmployeeType.PART_TIME]: 'Part time',
};

export const CONTRACT_STATUS_LABELS: Record<ContractStatus, string> = {
  [ContractStatus.DRAFT]: 'Draft',
  [ContractStatus.ACTIVE]: 'Active',
  [ContractStatus.EXPIRED]: 'Expired',
  [ContractStatus.CANCELLED]: 'Cancelled',
};

export const SCHEDULE_TYPE_LABELS: Record<WorkingScheduleType, string> = {
  [WorkingScheduleType.FULL_TIME]: 'Full time',
  [WorkingScheduleType.PART_TIME]: 'Part time',
  [WorkingScheduleType.FLEXIBLE]: 'Flexible',
};

export const WEEKDAY_LABELS: Record<Weekday, string> = {
  [Weekday.MONDAY]: 'Monday',
  [Weekday.TUESDAY]: 'Tuesday',
  [Weekday.WEDNESDAY]: 'Wednesday',
  [Weekday.THURSDAY]: 'Thursday',
  [Weekday.FRIDAY]: 'Friday',
  [Weekday.SATURDAY]: 'Saturday',
  [Weekday.SUNDAY]: 'Sunday',
};

export interface DepartmentRef {
  id: string;
  name: string;
  code: string;
}

export interface EmployeeRef {
  id: string;
  first_name: string;
  last_name: string;
  work_email: string;
  job_position: string | null;
  status: EmployeeStatus;
}

export interface WorkingScheduleRef {
  id: string;
  name: string;
  weekly_hours: number;
}

export interface SalaryStructureRef {
  id: string;
  name: string;
  code: string;
}

export interface Employee {
  id: string;
  first_name: string;
  last_name: string;
  full_name: string;
  work_email: string;
  phone: string | null;
  job_position: string | null;
  employee_type: EmployeeType;
  status: EmployeeStatus;
  hire_date: string | null;
  exit_date: string | null;
  bank_account: string | null;
  department: DepartmentRef | null;
  manager: EmployeeRef | null;
  default_schedule: WorkingScheduleRef | null;
  version: number;
}

export interface EmployeeInput {
  first_name: string;
  last_name: string;
  work_email: string;
  phone?: string | null;
  job_position?: string | null;
  employee_type: EmployeeType;
  status: EmployeeStatus;
  hire_date?: string | null;
  exit_date?: string | null;
  bank_account?: string | null;
  department_id?: string | null;
  manager_id?: string | null;
  default_schedule_id?: string | null;
}

export interface SmartButtonCounts {
  contracts: number;
  attendance: number;
  time_off_requests: number;
  time_off_allocations: number;
}

export interface ScheduleLine {
  id: string;
  day_of_week: Weekday;
  start_time: string;
  end_time: string;
  break_minutes: number;
}

export interface ScheduleLineInput {
  day_of_week: Weekday;
  start_time: string;
  end_time: string;
  break_minutes: number;
}

export interface WorkingSchedule {
  id: string;
  name: string;
  schedule_type: WorkingScheduleType;
  /** Server-computed from `lines`. Never editable, never submitted (PS A3). */
  weekly_hours: string;
  lines: ScheduleLine[];
  version: number;
}

export interface Contract {
  id: string;
  wage: string;
  start_date: string;
  end_date: string | null;
  job_position: string | null;
  status: ContractStatus;
  notes: string | null;
  employee: EmployeeRef;
  department: DepartmentRef | null;
  /** Nested, not raw FK ids — the API speaks public_ids throughout, and the
   *  list shows the schedule's name and hours without a second request. */
  salary_structure: SalaryStructureRef | null;
  working_schedule: WorkingScheduleRef | null;
  /** Server-computed: is this the contract in force TODAY for its employee?
   *  Not derivable here — a page may not hold all of an employee's contracts. */
  is_currently_active: boolean;
  version: number;
}

export interface ContractInput {
  employee_id?: string;
  wage: string;
  start_date: string;
  end_date?: string | null;
  job_position?: string | null;
  status: ContractStatus;
  notes?: string | null;
  department_id?: string | null;
  salary_structure_id?: string | null;
  working_schedule_id?: string | null;
}
