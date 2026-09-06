import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { CalendarDays, Clock } from 'lucide-react';

import { StatusMessage } from '@/components/StatusMessage';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { ApiError, fetchApi } from '@/lib/api-client';
import { useHrCollection } from '@/hooks/useAttendanceTimeOff';
import type { Employee } from '@/types/hr';
import { EMPLOYEE_STATUS_LABELS, EMPLOYEE_TYPE_LABELS } from '@/types/hr';
import type { TimeOffAllocation } from '@/types/attendance-time-off';

/**
 * "View own profile" — PRD §4's first Employee user story, and the one surface
 * the role was missing.
 *
 * Read-only on purpose. Architecture §5's first row gives Employee **R** on
 * their own profile and nothing more; editing an employee record is an HR act,
 * and the server enforces that regardless of what this screen renders.
 *
 * Everything here comes from `/employees/me` and `/time-off-allocations/me`,
 * which scope by the signed `employee_id` claim rather than by an id in the
 * URL — so there is no id to tamper with, and the page cannot be pointed at
 * somebody else by editing the address bar.
 *
 * A login with no Employee row behind it (every payroll/admin account, by
 * design) gets a 404 from those routes. That is an empty result, not an error
 * worth alarming anyone about, so it is rendered as an explanation.
 */
export function MyProfilePage() {
  const navigate = useNavigate();
  const profile = useQuery({
    queryKey: ['employees', 'me'],
    queryFn: () => fetchApi<Employee>('/employees/me'),
    retry: false,
  });
  const allocations = useHrCollection<TimeOffAllocation>('time-off-allocations');

  if (profile.isLoading) {
    return <p className="text-sm text-slate-400">Loading your profile…</p>;
  }

  if (profile.error) {
    // The STATUS, not the wording. This used to sniff the message for
    // /not found|no employee/, which the server's actual sentence ("This login
    // is not linked to an employee record") does not match — so the explanation
    // below never rendered and every admin/payroll login got a red
    // "Something went wrong" instead. A 404 from a route that scopes by the
    // signed claim means exactly one thing, and it is not an error.
    const unlinked = profile.error instanceof ApiError && profile.error.isNotFound;
    return unlinked ? (
      <Card>
        <CardContent className="p-6 space-y-2">
          <h1 className="text-lg font-semibold text-slate-100">No employee record</h1>
          <p className="text-sm text-slate-400">
            This login is not linked to an employee record, so there is no profile to show. A
            payroll or admin account normally has none — it is a login, not a person on the
            payroll.
          </p>
        </CardContent>
      </Card>
    ) : (
      <StatusMessage error={profile.error} />
    );
  }

  const me = profile.data!;
  const balances = allocations.data?.items ?? [];

  const facts: Array<[string, string]> = [
    ['Job position', me.job_position ?? '—'],
    ['Department', me.department?.name ?? '—'],
    [
      'Manager',
      me.manager ? `${me.manager.first_name} ${me.manager.last_name}` : '—',
    ],
    ['Employment type', EMPLOYEE_TYPE_LABELS[me.employee_type] ?? me.employee_type],
    ['Working schedule', me.default_schedule?.name ?? '—'],
    ['Hire date', me.hire_date ?? '—'],
    ['Work email', me.work_email],
    ['Phone', me.phone ?? '—'],
  ];

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">{me.full_name}</h1>
          <p className="text-sm text-slate-400">Your own record, as HR holds it.</p>
        </div>
        <Badge variant={me.status === 'active' ? 'default' : 'secondary'}>
          {EMPLOYEE_STATUS_LABELS[me.status] ?? me.status}
        </Badge>
      </header>

      <Card>
        <CardContent className="p-6">
          <dl className="grid gap-x-8 gap-y-4 sm:grid-cols-2">
            {facts.map(([label, value]) => (
              <div key={label}>
                <dt className="text-xs uppercase tracking-wide text-slate-500">{label}</dt>
                <dd className="text-sm text-slate-200">{value}</dd>
              </div>
            ))}
          </dl>
        </CardContent>
      </Card>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold text-slate-200">Leave balances</h2>
        <StatusMessage error={allocations.error} />
        {balances.length === 0 ? (
          <p className="text-sm text-slate-400">
            You have no leave allocations yet. HR creates these before leave can be requested
            against them.
          </p>
        ) : (
          <div className="grid gap-px overflow-hidden rounded-md border border-slate-800 bg-slate-800 sm:grid-cols-2 lg:grid-cols-3">
            {balances.map((allocation) => (
              <div key={allocation.id} className="bg-white p-4">
                  <p className="text-sm text-slate-200">{allocation.time_off_type.name}</p>
                  <p className="text-2xl font-semibold text-slate-100">{allocation.remaining}</p>
                  <p className="text-xs text-slate-500">
                    {allocation.taken} taken of {allocation.allocated} · valid from{' '}
                    {allocation.valid_from}
                    {allocation.valid_to ? ` to ${allocation.valid_to}` : ''}
                  </p>
              </div>
            ))}
          </div>
        )}
      </section>

      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={() => navigate('/attendance')}>
          <Clock className="mr-1.5 h-3.5 w-3.5" />
          My attendance
        </Button>
        <Button variant="outline" size="sm" onClick={() => navigate('/time-off')}>
          <CalendarDays className="mr-1.5 h-3.5 w-3.5" />
          My time off
        </Button>
      </div>
    </div>
  );
}
