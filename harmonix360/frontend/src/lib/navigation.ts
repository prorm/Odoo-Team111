import {
  BarChart3,
  CalendarDays,
  Clock,
  FileText,
  Users,
  Wallet,
  type LucideIcon,
} from 'lucide-react';

import { HR_ROLES, PAYROLL_ROLES, UserRole, hasRole } from '@/types/enums';

export interface NavItem {
  name: string;
  href: string;
  icon: LucideIcon;
  /** Roles that may see this entry. Presentation only — see the note below. */
  roles: readonly UserRole[];
}

/**
 * The top navigation from PS B1, in the order the problem statement lists it:
 * Employees, Contracts, Attendance, Time Off, Payroll, Reports.
 *
 * `roles` mirrors Architecture §5 so the nav does not offer a section that
 * would 403 on click. That is a COURTESY, NOT A CONTROL: every endpoint behind
 * these routes is independently guarded by `require_role` server-side. Anyone
 * can edit this array in their own browser; nobody can edit the server's
 * answer. Never move a permission decision here.
 *
 * Payroll and Reports are PAYROLL_ROLES — Architecture §5 gives HR Manager no
 * payroll access at all, which is the sharpest line in the matrix and the one
 * most easily blurred by a nav that shows everything to everyone.
 */
export const NAV_ITEMS: readonly NavItem[] = [
  { name: 'Employees', href: '/employees', icon: Users, roles: HR_ROLES },
  { name: 'Contracts', href: '/contracts', icon: FileText, roles: HR_ROLES },
  { name: 'Attendance', href: '/attendance', icon: Clock, roles: [...HR_ROLES, UserRole.EMPLOYEE] },
  { name: 'Time Off', href: '/time-off', icon: CalendarDays, roles: [...HR_ROLES, UserRole.EMPLOYEE] },
  { name: 'Payroll', href: '/payroll', icon: Wallet, roles: PAYROLL_ROLES },
  { name: 'Reports', href: '/reports', icon: BarChart3, roles: PAYROLL_ROLES },
];

export function visibleNavItems(role: UserRole | undefined): NavItem[] {
  return NAV_ITEMS.filter((item) => hasRole(role, item.roles));
}
