import * as React from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  AlertTriangle,
  CalendarCheck,
  FileText,
  ShieldAlert,
  TrendingUp,
  Users,
  Wallet,
  type LucideIcon,
} from 'lucide-react';

import { StatusMessage } from '@/components/StatusMessage';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { useDashboard } from '@/hooks/useDashboard';
import { fetchApi } from '@/lib/api-client';
import type {
  AttendanceOverview,
  ContractAttentionItem,
  DashboardFilterParams,
  DashboardResponse,
  DepartmentAmount,
  DepartmentBreakdownItem,
  MonthlyTrendPoint,
  PayrollWarning,
  TimeOffOverview,
} from '@/types/dashboard';
import type { DepartmentRef } from '@/types/hr';
import { EMPLOYEE_TYPE_LABELS, EmployeeType } from '@/types/hr';

/**
 * Payroll Dashboard (PS A7 / B9).
 *
 * One consolidated backend request per filter change (`useDashboard`) — every
 * widget below renders exactly what the server aggregated, never a client-side
 * re-filter of an already-loaded response. See docs/dashboard-data-contract.md
 * for what each number means and where it comes from.
 */

function currentMonthRange(): { start: string; end: string } {
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), 1);
  const end = new Date(now.getFullYear(), now.getMonth() + 1, 0);
  const fmt = (d: Date) => d.toISOString().slice(0, 10);
  return { start: fmt(start), end: fmt(end) };
}

/** Display-only formatting of a Decimal string. Never used in arithmetic —
 *  every total here was already computed server-side. */
function money(value: string): string {
  const n = Number(value);
  if (Number.isNaN(n)) return value;
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

const WARNING_LABELS: Record<string, string> = {
  missing_bank_details: 'Missing bank details',
  duplicate_payslip: 'Duplicate payslip',
  missing_contract: 'Missing contract',
  contract_attention: 'Contract attention',
  other: 'Other',
};

const ATTENDANCE_STATUS_LABELS: Record<string, string> = {
  present: 'Present',
  late: 'Late',
  absent: 'Absent',
  half_day: 'Half day',
  corrected: 'Corrected',
  overtime: 'Overtime',
  missing_checkout: 'Missing checkout',
};

const CHART_TOOLTIP_STYLE = {
  contentStyle: { background: '#0f172a', border: '1px solid #1e293b', borderRadius: 8, fontSize: 12 },
  labelStyle: { color: '#e2e8f0' },
  cursor: { fill: 'rgba(99, 102, 241, 0.08)' },
} as const;

export function ReportsPage() {
  const defaultRange = React.useMemo(currentMonthRange, []);
  const [periodStart, setPeriodStart] = React.useState(defaultRange.start);
  const [periodEnd, setPeriodEnd] = React.useState(defaultRange.end);
  const [departmentId, setDepartmentId] = React.useState('');
  const [employeeType, setEmployeeType] = React.useState('');

  const filters: DashboardFilterParams = {
    period_start: periodStart || undefined,
    period_end: periodEnd || undefined,
    department_id: departmentId || undefined,
    employee_type: (employeeType || undefined) as EmployeeType | undefined,
  };

  const { data, isLoading, isFetching, error } = useDashboard(filters);

  const { data: departments } = useQuery({
    queryKey: ['departments'],
    queryFn: () => fetchApi<DepartmentRef[]>('/departments/'),
    staleTime: 5 * 60 * 1000,
  });

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Payroll Dashboard</h1>
          <p className="mt-0.5 text-xs text-slate-400">
            Query-backed payroll and workforce analytics (PS A7 / B9) — every number reflects the filters below.
          </p>
        </div>
        {isFetching && !isLoading && <span className="text-xs text-slate-500">Refreshing…</span>}
      </header>

      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 p-4">
          <div>
            <label className="mb-1 block text-xs text-slate-400" htmlFor="dashboard-period-start">
              From
            </label>
            <Input
              id="dashboard-period-start"
              type="date"
              value={periodStart}
              onChange={(e) => setPeriodStart(e.target.value)}
              className="w-auto"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-slate-400" htmlFor="dashboard-period-end">
              To
            </label>
            <Input
              id="dashboard-period-end"
              type="date"
              value={periodEnd}
              onChange={(e) => setPeriodEnd(e.target.value)}
              className="w-auto"
            />
          </div>
          <Select
            value={departmentId}
            onChange={(e) => setDepartmentId(e.target.value)}
            aria-label="Filter by department"
            className="w-auto min-w-[170px]"
          >
            <option value="">All departments</option>
            {departments?.map((department) => (
              <option key={department.id} value={department.id}>
                {department.name}
              </option>
            ))}
          </Select>
          <Select
            value={employeeType}
            onChange={(e) => setEmployeeType(e.target.value)}
            aria-label="Filter by employee type"
            className="w-auto min-w-[170px]"
          >
            <option value="">All employee types</option>
            {Object.values(EmployeeType).map((value) => (
              <option key={value} value={value}>
                {EMPLOYEE_TYPE_LABELS[value]}
              </option>
            ))}
          </Select>
        </CardContent>
      </Card>

      <StatusMessage error={error} />

      {isLoading && <p className="text-sm text-slate-400">Loading dashboard…</p>}

      {data && (
        <>
          <KpiRow data={data} />

          <div className="grid gap-4 lg:grid-cols-2">
            <SalaryByDepartmentChart rows={data.salary_cost_by_department} />
            <MonthlyTrendChart rows={data.monthly_net_salary_trend} />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <WarningsCard warnings={data.warnings} />
            <ContractAttentionCard items={data.contract_attention} />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <AttendanceOverviewCard attendance={data.attendance} />
            <TimeOffOverviewCard timeOff={data.time_off} />
          </div>

          <DepartmentBreakdownTable rows={data.department_breakdown} />
        </>
      )}
    </div>
  );
}

// ------------------------------------------------------------------- KPIs

function KpiCard({
  icon: Icon,
  label,
  value,
  hint,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-center gap-1.5 text-xs text-slate-400">
          <Icon className="h-3.5 w-3.5" />
          {label}
        </div>
        <p className="mt-2 text-2xl font-semibold text-slate-100">{value}</p>
        {hint && <p className="mt-1 text-[11px] text-slate-500">{hint}</p>}
      </CardContent>
    </Card>
  );
}

function KpiRow({ data }: { data: DashboardResponse }) {
  const { kpis, attendance } = data;
  return (
    <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-5">
      <KpiCard icon={Wallet} label="Total Net Salary Paid" value={money(kpis.total_net_salary_paid)} />
      <KpiCard icon={FileText} label="Payslips Generated" value={String(kpis.payslips_generated)} />
      <KpiCard icon={TrendingUp} label="Average Salary" value={money(kpis.average_salary)} />
      <KpiCard icon={CalendarCheck} label="Approved Time Off" value={String(kpis.approved_time_off)} />
      <KpiCard
        icon={Users}
        label="Attendance Health"
        value={kpis.attendance_health_pct !== null ? `${kpis.attendance_health_pct}%` : '—'}
        hint={
          attendance.expected_working_days > 0
            ? `${attendance.attended_days} / ${attendance.expected_working_days} expected days`
            : 'No scheduled employees in this filter'
        }
      />
    </div>
  );
}

// ------------------------------------------------------------------ charts

function EmptyChartState({ message }: { message: string }) {
  return <p className="py-16 text-center text-sm text-slate-500">{message}</p>;
}

function SalaryByDepartmentChart({ rows }: { rows: DepartmentAmount[] }) {
  const chartData = rows.map((row) => ({ department: row.department, amount: Number(row.amount) }));
  return (
    <Card>
      <CardHeader>
        <CardTitle>Salary Cost by Department</CardTitle>
        <CardDescription>Paid net payroll for the selected period.</CardDescription>
      </CardHeader>
      <CardContent>
        {chartData.length === 0 ? (
          <EmptyChartState message="No paid payroll in this period yet." />
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
              <XAxis dataKey="department" tick={{ fill: '#94a3b8', fontSize: 12 }} axisLine={{ stroke: '#1e293b' }} />
              <YAxis tick={{ fill: '#94a3b8', fontSize: 12 }} axisLine={{ stroke: '#1e293b' }} />
              <Tooltip {...CHART_TOOLTIP_STYLE} formatter={(value: number) => money(String(value))} />
              <Bar dataKey="amount" name="Net paid" fill="#6366f1" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}

function MonthlyTrendChart({ rows }: { rows: MonthlyTrendPoint[] }) {
  const chartData = rows.map((row) => ({ month: row.month, amount: Number(row.amount) }));
  return (
    <Card>
      <CardHeader>
        <CardTitle>Monthly Net Salary Trend</CardTitle>
        <CardDescription>Paid net payroll by payrun month.</CardDescription>
      </CardHeader>
      <CardContent>
        {chartData.length === 0 ? (
          <EmptyChartState message="No paid payroll in this period yet." />
        ) : (
          <ResponsiveContainer width="100%" height={260}>
            <LineChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
              <XAxis dataKey="month" tick={{ fill: '#94a3b8', fontSize: 12 }} axisLine={{ stroke: '#1e293b' }} />
              <YAxis tick={{ fill: '#94a3b8', fontSize: 12 }} axisLine={{ stroke: '#1e293b' }} />
              <Tooltip {...CHART_TOOLTIP_STYLE} formatter={(value: number) => money(String(value))} />
              <Line type="monotone" dataKey="amount" name="Net paid" stroke="#22d3ee" strokeWidth={2} dot={{ r: 3 }} />
            </LineChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}

// ----------------------------------------------------------------- alerts

function WarningsCard({ warnings }: { warnings: PayrollWarning[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-amber-400" />
          Payroll Warnings
        </CardTitle>
        <CardDescription>Deterministic checks against real payslip data.</CardDescription>
      </CardHeader>
      <CardContent>
        {warnings.length === 0 ? (
          <p className="py-6 text-center text-sm text-slate-500">No payroll warnings for this period.</p>
        ) : (
          <ul className="space-y-2">
            {warnings.map((warning, index) => (
              <li
                key={`${warning.payslip_id}-${index}`}
                className="flex items-start justify-between gap-3 rounded-lg border border-slate-800 bg-slate-950/40 p-3"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-slate-200">{warning.employee_name}</p>
                  <p className="mt-0.5 text-xs text-slate-400">{warning.message}</p>
                </div>
                <Badge variant="warning" className="flex-shrink-0">
                  {WARNING_LABELS[warning.category] ?? warning.category}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function ContractAttentionCard({ items }: { items: ContractAttentionItem[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldAlert className="h-4 w-4 text-rose-400" />
          Contract Attention
        </CardTitle>
        <CardDescription>Contracts expiring within 30 days, and active employees with none.</CardDescription>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="py-6 text-center text-sm text-slate-500">No contract issues right now.</p>
        ) : (
          <ul className="space-y-2">
            {items.map((item, index) => (
              <li
                key={`${item.employee_id}-${index}`}
                className="flex items-start justify-between gap-3 rounded-lg border border-slate-800 bg-slate-950/40 p-3"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm text-slate-200">{item.employee_name}</p>
                  <p className="mt-0.5 text-xs text-slate-400">{item.detail}</p>
                </div>
                <Badge variant={item.kind === 'missing_contract' ? 'destructive' : 'warning'} className="flex-shrink-0">
                  {item.kind === 'missing_contract' ? 'No active contract' : 'Expiring soon'}
                </Badge>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// -------------------------------------------------------------- overviews

function AttendanceOverviewCard({ attendance }: { attendance: AttendanceOverview }) {
  const total = attendance.by_status.reduce((sum, row) => sum + row.count, 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Attendance Overview</CardTitle>
        <CardDescription>Recorded attendance by status for the selected period.</CardDescription>
      </CardHeader>
      <CardContent>
        {total === 0 ? (
          <p className="py-6 text-center text-sm text-slate-500">No attendance recorded in this period.</p>
        ) : (
          <ul className="space-y-2">
            {attendance.by_status
              .filter((row) => row.count > 0)
              .map((row) => (
                <li key={row.status} className="flex items-center justify-between text-sm">
                  <span className="text-slate-300">{ATTENDANCE_STATUS_LABELS[row.status] ?? row.status}</span>
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 w-24 overflow-hidden rounded-full bg-slate-800">
                      <div
                        className="h-full rounded-full bg-indigo-500"
                        style={{ width: `${total > 0 ? (row.count / total) * 100 : 0}%` }}
                      />
                    </div>
                    <span className="w-8 text-right text-slate-400">{row.count}</span>
                  </div>
                </li>
              ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function TimeOffOverviewCard({ timeOff }: { timeOff: TimeOffOverview }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Time-Off Overview</CardTitle>
        <CardDescription>Request status and current leave balances.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-3 gap-2 text-center">
          <div className="rounded-lg border border-slate-800 bg-slate-950/40 py-2">
            <p className="text-lg font-semibold text-amber-300">{timeOff.pending}</p>
            <p className="text-[11px] text-slate-500">Pending</p>
          </div>
          <div className="rounded-lg border border-slate-800 bg-slate-950/40 py-2">
            <p className="text-lg font-semibold text-emerald-300">{timeOff.approved}</p>
            <p className="text-[11px] text-slate-500">Approved</p>
          </div>
          <div className="rounded-lg border border-slate-800 bg-slate-950/40 py-2">
            <p className="text-lg font-semibold text-rose-300">{timeOff.refused}</p>
            <p className="text-[11px] text-slate-500">Refused</p>
          </div>
        </div>

        {timeOff.balance_summary.length === 0 ? (
          <p className="py-2 text-center text-sm text-slate-500">No active leave allocations.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-slate-800">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Leave type</TableHead>
                  <TableHead>Allocated</TableHead>
                  <TableHead>Taken</TableHead>
                  <TableHead>Remaining</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {timeOff.balance_summary.map((row) => (
                  <TableRow key={row.time_off_type_id}>
                    <TableCell>{row.time_off_type}</TableCell>
                    <TableCell>{money(row.allocated)}</TableCell>
                    <TableCell>{money(row.taken)}</TableCell>
                    <TableCell>{money(row.remaining)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function DepartmentBreakdownTable({ rows }: { rows: DepartmentBreakdownItem[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Department Breakdown</CardTitle>
        <CardDescription>Headcount and paid payroll spend, per department.</CardDescription>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="py-6 text-center text-sm text-slate-500">No employees match these filters.</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-slate-800">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Department</TableHead>
                  <TableHead>Headcount</TableHead>
                  <TableHead>Payroll spend</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((row) => (
                  <TableRow key={row.department_id ?? 'unassigned'}>
                    <TableCell>{row.department}</TableCell>
                    <TableCell>{row.headcount}</TableCell>
                    <TableCell>{money(row.payroll_spend)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
