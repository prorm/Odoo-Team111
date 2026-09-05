import {
  BarChart3,
  Bot,
  ShieldAlert,
  CalendarDays,
  Clock,
  FileText,
  UserCircle,
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
  // Employee-only, and deliberately first for that role: PRD §4's Employee
  // stories start at "view own profile", and every other entry an Employee can
  // see is a log of something rather than a record of them. HR roles already
  // reach any employee through Employees, and their logins usually have no
  // Employee row at all, so the entry would lead them to an empty page.
  { name: 'My Profile', href: '/my-profile', icon: UserCircle, roles: [UserRole.EMPLOYEE] },
  { name: 'Employees', href: '/employees', icon: Users, roles: HR_ROLES },
  { name: 'Contracts', href: '/contracts', icon: FileText, roles: HR_ROLES },
  { name: 'Attendance', href: '/attendance', icon: Clock, roles: [...HR_ROLES, UserRole.EMPLOYEE] },
  { name: 'Time Off', href: '/time-off', icon: CalendarDays, roles: [...HR_ROLES, UserRole.EMPLOYEE] },
  { name: 'Payroll', href: '/payroll', icon: Wallet, roles: PAYROLL_ROLES },
  { name: 'Reports', href: '/reports', icon: BarChart3, roles: PAYROLL_ROLES },
  // PS §5.1. Visible to everyone with a login, because the screen does two
  // different jobs for two different audiences: payroll roles ask contextual
  // questions about records, and an Employee uses the propose-and-confirm flow
  // to request their own leave. The payroll half hides itself for a role that
  // cannot read payroll — and the server refuses it regardless, which is the
  // control; this is only what gets rendered.
  {
    name: 'Assistant',
    href: '/assistant',
    icon: Bot,
    roles: [...HR_ROLES, ...PAYROLL_ROLES, UserRole.EMPLOYEE],
  },
  // PS §5.7. Payroll roles only: every check reads payroll or the records that
  // gate it, and Architecture §5 gives HR Manager no payroll access.
  { name: 'Anomalies', href: '/anomalies', icon: ShieldAlert, roles: PAYROLL_ROLES },
];

export function visibleNavItems(role: UserRole | undefined): NavItem[] {
  return NAV_ITEMS.filter((item) => hasRole(role, item.roles));
}
