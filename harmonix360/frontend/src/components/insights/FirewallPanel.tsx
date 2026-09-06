import { Link } from 'react-router-dom';
import { CheckCircle2, ShieldAlert, ShieldCheck } from 'lucide-react';

import { StatusMessage } from '@/components/StatusMessage';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { usePayrunFirewall } from '@/hooks/useInsights';

/**
 * The Payroll Validation Firewall (PRD §5.10).
 *
 * Groups the payrun's findings by code, names the fix for each, and links
 * straight to the record that has to change. Revalidate is the caller's
 * existing mutation — this component takes it as a prop rather than owning
 * one, because the transition already has exactly one client-side path
 * (`usePayrunTransition('validate')`) and a second would be a second way to
 * finalize payroll.
 *
 * The gate state is the SERVER's answer, rendered. `can_validate` and
 * `gate_message` both come from the backend; this component never decides that
 * a run is finalizable, and disabling the button is a courtesy on top of a
 * refusal the server issues regardless.
 */
export function FirewallPanel({
  payrunId,
  onRevalidate,
  revalidating,
  revalidateError,
}: {
  payrunId: string;
  onRevalidate?: () => void;
  revalidating?: boolean;
  revalidateError?: unknown;
}) {
  const firewall = usePayrunFirewall(payrunId);

  if (firewall.isLoading) {
    return <p className="text-sm text-slate-400">Checking the payroll gate…</p>;
  }
  if (firewall.error) return <StatusMessage error={firewall.error} />;
  if (!firewall.data) return null;

  const report = firewall.data;
  const clear = report.blocking_count === 0;

  return (
    <section className="rounded-md border border-slate-800 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          {clear ? (
            <ShieldCheck className="mt-0.5 h-5 w-5 text-emerald-400" />
          ) : (
            <ShieldAlert className="mt-0.5 h-5 w-5 text-rose-400" />
          )}
          <div>
            <h3 className="font-medium text-slate-100">Validation firewall</h3>
            <p className="mt-0.5 max-w-2xl text-sm text-slate-400">{report.gate_message}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={report.blocking_count ? 'destructive' : 'success'}>
            {report.blocking_count} blocking
          </Badge>
          {report.advisory_count > 0 && (
            <Badge variant="warning">{report.advisory_count} advisory</Badge>
          )}
          {onRevalidate && (
            <Button
              size="sm"
              variant="outline"
              onClick={onRevalidate}
              disabled={revalidating || !report.can_validate}
              title={
                report.can_validate
                  ? 'Run the blocking-issue checks and validate'
                  : report.gate_message
              }
            >
              <CheckCircle2 className="mr-2 h-4 w-4" />
              {revalidating ? 'Revalidating…' : 'Revalidate'}
            </Button>
          )}
        </div>
      </div>

      {revalidateError ? (
        <div className="mt-3">
          <StatusMessage error={revalidateError} />
        </div>
      ) : null}

      {report.groups.length === 0 ? (
        <p className="mt-4 text-sm text-slate-400">
          No findings on this payrun.
        </p>
      ) : (
        <ul className="mt-4 space-y-3">
          {report.groups.map((group) => (
            <li
              key={group.code}
              className={`rounded-md border p-3 ${
                group.severity === 'blocking'
                  ? 'border-rose-800/60 bg-rose-950/30'
                  : 'border-amber-800/50 bg-amber-950/20'
              }`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  <span className="font-medium text-slate-100">{group.title}</span>
                  <Badge variant={group.severity === 'blocking' ? 'destructive' : 'warning'}>
                    {group.severity}
                  </Badge>
                  <Badge variant="secondary">{group.count}</Badge>
                  {/* A code with no guidance entry is shown, never hidden: a
                      blocking issue nobody can see still blocks. */}
                  {!group.recognized && (
                    <Badge variant="outline" title={group.code}>
                      unrecognised code
                    </Badge>
                  )}
                </div>
                <span className="font-mono text-[11px] text-slate-500">{group.code}</span>
              </div>

              <p className="mt-1 text-sm text-slate-300">{group.fix}</p>

              <ul className="mt-2 space-y-1">
                {group.issues.map((issue, index) => (
                  <li
                    key={`${group.code}-${index}`}
                    className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-slate-950/50 px-2 py-1.5 text-sm"
                  >
                    <span className="text-slate-300">
                      {issue.employee_name ? (
                        <span className="font-medium text-slate-100">{issue.employee_name}: </span>
                      ) : null}
                      {issue.message}
                    </span>
                    <Link
                      to={issue.navigate_to}
                      className="shrink-0 text-xs text-indigo-400 underline-offset-4 hover:underline"
                    >
                      Open record
                    </Link>
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
