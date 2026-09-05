import * as React from 'react';
import { Link } from 'react-router-dom';
import { CalendarClock, LayoutGrid, List, Plus, Search } from 'lucide-react';

import { StatusMessage } from '@/components/StatusMessage';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useEmployees } from '@/hooks/useHrApi';
import { fetchApi } from '@/lib/api-client';
import { useQuery } from '@tanstack/react-query';
import type { DepartmentRef, Employee } from '@/types/hr';
import {
  EMPLOYEE_STATUS_LABELS,
  EMPLOYEE_TYPE_LABELS,
  EmployeeStatus,
} from '@/types/hr';

import { EmployeeForm } from './EmployeeForm';

type ViewMode = 'kanban' | 'list';

const STATUS_VARIANT: Record<EmployeeStatus, 'success' | 'warning' | 'secondary' | 'outline'> = {
  [EmployeeStatus.ACTIVE]: 'success',
  [EmployeeStatus.ON_LEAVE]: 'warning',
  [EmployeeStatus.NOTICE_PERIOD]: 'warning',
  [EmployeeStatus.EXITED]: 'secondary',
};

/**
 * Employee Master (PS A1): Kanban + List + Form.
 *
 * KANBAN GROUPING — grouped by DEPARTMENT, not status.
 *
 * Both were plausible and the PS does not specify, so: a Kanban board earns its
 * place when the columns are things a person compares across, and the everyday
 * HR question is "who is in Engineering, and who reports to whom" far more
 * often than "who is on notice period". Status is also badly distributed — in
 * a healthy organisation almost every card lands in `active` and the other
 * three columns sit empty, which is a worse board than a list. Status is
 * available as a filter instead, which answers the same question without
 * spending the whole layout on it.
 *
 * Grouping happens client-side over the fetched page rather than through a
 * grouped endpoint: the grouping key is already on every row, and a second
 * endpoint would be a second place for the filters to drift out of step.
 */
export function EmployeesPage() {
  const [view, setView] = React.useState<ViewMode>('kanban');
  const [search, setSearch] = React.useState('');
  const [debouncedSearch, setDebouncedSearch] = React.useState('');
  const [departmentId, setDepartmentId] = React.useState('');
  const [status, setStatus] = React.useState('');
  const [formOpen, setFormOpen] = React.useState(false);

  React.useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(search), 250);
    return () => clearTimeout(timer);
  }, [search]);

  const { data, isLoading, error } = useEmployees({
    search: debouncedSearch || undefined,
    department_id: departmentId || undefined,
    status: status || undefined,
    limit: 200,
  });

  const { data: departments } = useQuery({
    queryKey: ['departments'],
    queryFn: () => fetchApi<DepartmentRef[]>('/departments/'),
    staleTime: 5 * 60 * 1000,
  });

  const employees = data?.items ?? [];

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Employees</h1>
          <p className="text-xs text-slate-400 mt-0.5">
            {data ? `${data.total} employee${data.total === 1 ? '' : 's'}` : 'Loading…'}
          </p>
        </div>

        <div className="flex items-center gap-2">
          {/* Working Schedules is A3 configuration, not one of PS B1's six top-
              level sections, so it lives under Employees — the only screen that
              assigns one — rather than as a seventh nav entry the PS does not
              list. */}
          <Link to="/employees/schedules">
            <Button variant="outline" size="sm">
              <CalendarClock className="mr-1.5 h-3.5 w-3.5" />
              Working schedules
            </Button>
          </Link>
          <Button size="sm" onClick={() => setFormOpen(true)}>
            <Plus className="mr-1.5 h-3.5 w-3.5" />
            New employee
          </Button>
        </div>
      </header>

      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 p-4">
          <div className="relative min-w-[220px] flex-1">
            <Search className="absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search name, email or position…"
              aria-label="Search employees"
              className="pl-9"
            />
          </div>

          <Select
            value={departmentId}
            onChange={(e) => setDepartmentId(e.target.value)}
            aria-label="Filter by department"
            className="w-auto min-w-[160px]"
          >
            <option value="">All departments</option>
            {departments?.map((department) => (
              <option key={department.id} value={department.id}>
                {department.name}
              </option>
            ))}
          </Select>

          <Select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Filter by status"
            className="w-auto min-w-[150px]"
          >
            <option value="">All statuses</option>
            {Object.values(EmployeeStatus).map((value) => (
              <option key={value} value={value}>
                {EMPLOYEE_STATUS_LABELS[value]}
              </option>
            ))}
          </Select>

          <div
            role="group"
            aria-label="View mode"
            className="flex rounded-lg border border-slate-800 bg-slate-950/60 p-0.5"
          >
            <ViewToggle active={view === 'kanban'} onClick={() => setView('kanban')} label="Kanban">
              <LayoutGrid className="h-3.5 w-3.5" />
            </ViewToggle>
            <ViewToggle active={view === 'list'} onClick={() => setView('list')} label="List">
              <List className="h-3.5 w-3.5" />
            </ViewToggle>
          </div>
        </CardContent>
      </Card>

      <StatusMessage error={error} />

      {isLoading && <p className="text-sm text-slate-400">Loading employees…</p>}

      {!isLoading && employees.length === 0 && (
        <Card>
          <CardContent className="py-12 text-center">
            <p className="text-sm text-slate-300">No employees match these filters.</p>
            <p className="mt-1 text-xs text-slate-500">
              {search || departmentId || status
                ? 'Try clearing a filter, or add someone new.'
                : 'Create the first employee to get started.'}
            </p>
          </CardContent>
        </Card>
      )}

      {!isLoading && employees.length > 0 && view === 'kanban' && <KanbanView employees={employees} />}
      {!isLoading && employees.length > 0 && view === 'list' && <ListView employees={employees} />}

      <EmployeeForm open={formOpen} onOpenChange={setFormOpen} />
    </div>
  );
}

function ViewToggle({
  active,
  onClick,
  label,
  children,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs transition-colors ${
        active ? 'bg-indigo-600/20 text-indigo-300' : 'text-slate-400 hover:text-slate-200'
      }`}
    >
      {children}
      {label}
    </button>
  );
}

function KanbanView({ employees }: { employees: Employee[] }) {
  const groups = React.useMemo(() => {
    const byDepartment = new Map<string, { name: string; employees: Employee[] }>();
    for (const employee of employees) {
      // Employees with no department get their own column rather than being
      // hidden — an unassigned employee is exactly the record HR needs to find.
      const key = employee.department?.id ?? '__unassigned__';
      const name = employee.department?.name ?? 'Unassigned';
      if (!byDepartment.has(key)) byDepartment.set(key, { name, employees: [] });
      byDepartment.get(key)!.employees.push(employee);
    }
    return [...byDepartment.values()].sort((a, b) => {
      if (a.name === 'Unassigned') return 1;
      if (b.name === 'Unassigned') return -1;
      return a.name.localeCompare(b.name);
    });
  }, [employees]);

  return (
    // Horizontal scroll lives on this container, never on the page body.
    <div className="flex gap-4 overflow-x-auto pb-2">
      {groups.map((group) => (
        <section key={group.name} className="w-72 flex-shrink-0">
          <div className="mb-2 flex items-center justify-between px-1">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              {group.name}
            </h2>
            <span className="text-[11px] text-slate-500">{group.employees.length}</span>
          </div>
          <div className="space-y-2">
            {group.employees.map((employee) => (
              <EmployeeCard key={employee.id} employee={employee} />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function EmployeeCard({ employee }: { employee: Employee }) {
  return (
    <Link to={`/employees/${employee.id}`} className="block">
      <Card className="p-3 transition-colors hover:border-indigo-500/40">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-slate-100">{employee.full_name}</p>
            <p className="truncate text-[11px] text-slate-400">
              {employee.job_position ?? employee.work_email}
            </p>
          </div>
          <Badge variant={STATUS_VARIANT[employee.status]} className="flex-shrink-0 text-[9px]">
            {EMPLOYEE_STATUS_LABELS[employee.status]}
          </Badge>
        </div>
        {employee.manager && (
          <p className="mt-2 truncate text-[11px] text-slate-500">
            Reports to {employee.manager.first_name} {employee.manager.last_name}
          </p>
        )}
      </Card>
    </Link>
  );
}

function ListView({ employees }: { employees: Employee[] }) {
  return (
    <Card>
      {/* The table scrolls inside its own container; the page never scrolls
          sideways. */}
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Position</TableHead>
              <TableHead>Department</TableHead>
              <TableHead>Manager</TableHead>
              <TableHead>Schedule</TableHead>
              <TableHead>Type</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {employees.map((employee) => (
              <TableRow key={employee.id}>
                <TableCell>
                  <Link
                    to={`/employees/${employee.id}`}
                    className="font-medium text-slate-100 hover:text-indigo-300"
                  >
                    {employee.full_name}
                  </Link>
                  <span className="block text-[11px] text-slate-500">{employee.work_email}</span>
                </TableCell>
                <TableCell className="text-slate-300">{employee.job_position ?? '—'}</TableCell>
                <TableCell className="text-slate-300">{employee.department?.name ?? '—'}</TableCell>
                <TableCell className="text-slate-300">
                  {employee.manager ? `${employee.manager.first_name} ${employee.manager.last_name}` : '—'}
                </TableCell>
                <TableCell className="text-slate-300">
                  {employee.default_schedule
                    ? `${employee.default_schedule.name} (${employee.default_schedule.weekly_hours}h)`
                    : '—'}
                </TableCell>
                <TableCell className="text-slate-300">
                  {EMPLOYEE_TYPE_LABELS[employee.employee_type]}
                </TableCell>
                <TableCell>
                  <Badge variant={STATUS_VARIANT[employee.status]} className="text-[9px]">
                    {EMPLOYEE_STATUS_LABELS[employee.status]}
                  </Badge>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </Card>
  );
}
