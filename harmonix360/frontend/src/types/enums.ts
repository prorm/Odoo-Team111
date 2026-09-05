/** PeoplePay360's five roles — PRD §3, Architecture §5.
 *
 * Values must stay byte-identical to app/models/enums.py's UserRole, because
 * they cross the wire verbatim in the JWT `role` claim and in every API
 * response. Lowercase, underscore-separated.
 *
 * These drive presentation only. Every action they gate is independently
 * role-checked server-side (app/api/v1/deps.py `require_role`) — hiding a nav
 * entry or a button here is a courtesy to the user, never a control. */
export enum UserRole {
  EMPLOYEE = 'employee',
  HR_MANAGER = 'hr_manager',
  HR_PAYROLL_USER = 'hr_payroll_user',
  HR_PAYROLL_MANAGER = 'hr_payroll_manager',
  ADMIN = 'admin',
}

/** Human-readable labels, for badges and role pickers. */
export const ROLE_LABELS: Record<UserRole, string> = {
  [UserRole.EMPLOYEE]: 'Employee',
  [UserRole.HR_MANAGER]: 'HR Manager',
  [UserRole.HR_PAYROLL_USER]: 'HR Payroll User',
  [UserRole.HR_PAYROLL_MANAGER]: 'HR Payroll Manager',
  [UserRole.ADMIN]: 'Admin',
};

/** Full CRUD on Employees, Contracts, Schedules, Attendance, Time Off. */
export const HR_ROLES: readonly UserRole[] = [
  UserRole.HR_MANAGER,
  UserRole.HR_PAYROLL_USER,
  UserRole.HR_PAYROLL_MANAGER,
  UserRole.ADMIN,
];

/** Payruns/Payslips, plus read-only Salary Structures/Rules. HR Manager is
 *  deliberately absent: PRD §3 gives that role no payroll access at all. */
export const PAYROLL_ROLES: readonly UserRole[] = [
  UserRole.HR_PAYROLL_USER,
  UserRole.HR_PAYROLL_MANAGER,
  UserRole.ADMIN,
];

/** Authoring Salary Structures and Rules. */
export const PAYROLL_ADMIN_ROLES: readonly UserRole[] = [
  UserRole.HR_PAYROLL_MANAGER,
  UserRole.ADMIN,
];

export function hasRole(role: UserRole | undefined, allowed: readonly UserRole[]): boolean {
  if (!role) return false;
  return role === UserRole.ADMIN || allowed.includes(role);
}

export enum UserStatus {
  ACTIVE = 'ACTIVE',
  INACTIVE = 'INACTIVE',
}
