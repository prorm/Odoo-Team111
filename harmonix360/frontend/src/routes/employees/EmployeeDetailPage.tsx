import * as React from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  ArrowLeft,
  CalendarCheck,
  CalendarDays,
  Clock,
  FileText,
  Pencil,
} from 'lucide-react';

import { StatusMessage } from '@/components/StatusMessage';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { useEmployee, useSmartButtonCounts } from '@/hooks/useHrApi';
import { EMPLOYEE_STATUS_LABELS, EMPLOYEE_TYPE_LABELS } from '@/types/hr';

import { EmployeeForm } from './EmployeeForm';

/**
 * The Employee form as a hub (PS B2), with smart-button navigation to filtered
 * related records.
 *
 * Every smart button carries the employee id in the target URL, so the
 * destination opens already filtered rather than showing everyone and asking
 * the user to filter again. Attendance and Time Off have no screens until
 * Phase 2 — their buttons link to the section and read 0, which is the honest
 * state — but the counts and the navigation pattern are wired now, so those
 * phases only supply the query.
 */
export function EmployeeDetailPage() {
  const { employeeId } = useParams<{ employeeId: string }>();
  const navigate = useNavigate();
  const [editing, setEditing] = React.useState(false);

  const { data: employee, isLoading, error } = useEmployee(employeeId);
  const { data: counts } = useSmartButtonCounts(employeeId);

  if (isLoading) return <p className="text-sm text-slate-400">Loading employee…</p>;
  if (error) return <StatusMessage error={error} />;
  if (!employee) return null;

  return (
    <div className="space-y-5">
      <div>
        <Button variant="ghost" size="sm" onClick={() => navigate('/employees')} className="-ml-2">
          <ArrowLeft className="mr-1.5 h-3.5 w-3.5" />
          All employees
        </Button>
      </div>

      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-xl font-semibold text-slate-100">{employee.full_name}</h1>
            <Badge variant={employee.status === 'active' ? 'success' : 'secondary'} className="text-[9px]">
              {EMPLOYEE_STATUS_LABELS[employee.status]}
            </Badge>
          </div>
          <p className="mt-0.5 text-xs text-slate-400">
            {employee.job_position ?? 'No position set'}
            {employee.department ? ` · ${employee.department.name}` : ''}
          </p>
        </div>
        <Button size="sm" onClick={() => setEditing(true)}>
          <Pencil className="mr-1.5 h-3.5 w-3.5" />
          Edit
        </Button>
      </header>

      {/* Smart buttons (PS B2) */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <SmartButton
          to={`/contracts?employee=${employee.id}`}
          icon={FileText}
          label="Contracts"
          count={counts?.contracts}
        />
        <SmartButton
          to={`/attendance?employee=${employee.id}`}
          icon={Clock}
          label="Attendance"
          count={counts?.attendance}
          pending="Screens land in Phase 2"
        />
        <SmartButton
          to={`/time-off?employee=${employee.id}&tab=requests`}
          icon={CalendarDays}
          label="Time off requests"
          count={counts?.time_off_requests}
          pending="Screens land in Phase 2"
        />
        <SmartButton
          to={`/time-off?employee=${employee.id}&tab=allocations`}
          icon={CalendarCheck}
          label="Allocations"
          count={counts?.time_off_allocations}
          pending="Screens land in Phase 2"
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Identity</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2.5">
            <Detail label="Work email" value={employee.work_email} />
            <Detail label="Phone" value={employee.phone} />
            <Detail label="Employment type" value={EMPLOYEE_TYPE_LABELS[employee.employee_type]} />
            <Detail label="Hire date" value={employee.hire_date} />
            <Detail label="Exit date" value={employee.exit_date} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Organisation &amp; working time</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2.5">
            <Detail label="Department" value={employee.department?.name} />
            <Detail
              label="Manager"
              value={
                employee.manager ? (
                  <Link
                    to={`/employees/${employee.manager.id}`}
                    className="text-indigo-300 hover:text-indigo-200"
                  >
                    {employee.manager.first_name} {employee.manager.last_name}
                  </Link>
                ) : null
              }
            />
            <Detail
              label="Working schedule"
              value={
                employee.default_schedule
                  ? `${employee.default_schedule.name} — ${employee.default_schedule.weekly_hours}h/week`
                  : null
              }
            />
            <Detail
              label="Bank account"
              value={employee.bank_account}
              warningWhenEmpty="Missing — this will block payroll finalisation"
            />
          </CardContent>
        </Card>
      </div>

      <EmployeeForm open={editing} onOpenChange={setEditing} employee={employee} />
    </div>
  );
}

function SmartButton({
  to,
  icon: Icon,
  label,
  count,
  pending,
}: {
  to: string;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  count?: number;
  pending?: string;
}) {
  return (
    <Link to={to}>
      <Card className="p-4 transition-colors hover:border-indigo-500/40">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-slate-800/80">
            <Icon className="h-4 w-4 text-indigo-300" />
          </div>
          <div className="min-w-0">
            {/* `count === undefined` is "still loading", which is different from
                zero — showing 0 while the request is in flight makes a real 0
                indistinguishable from a slow one. */}
            <p className="text-lg font-semibold leading-tight text-slate-100">
              {count === undefined ? '—' : count}
            </p>
            <p className="truncate text-[11px] text-slate-400">{label}</p>
            {pending && <p className="truncate text-[10px] text-slate-500">{pending}</p>}
          </div>
        </div>
      </Card>
    </Link>
  );
}

function Detail({
  label,
  value,
  warningWhenEmpty,
}: {
  label: string;
  value: React.ReactNode;
  warningWhenEmpty?: string;
}) {
  const isEmpty = value === null || value === undefined || value === '';

  return (
    <div className="flex items-baseline justify-between gap-3">
      <span className="text-[11px] text-slate-500">{label}</span>
      {isEmpty && warningWhenEmpty ? (
        <span className="text-right text-xs text-amber-300">{warningWhenEmpty}</span>
      ) : (
        <span className="text-right text-xs text-slate-200">{isEmpty ? '—' : value}</span>
      )}
    </div>
  );
}
