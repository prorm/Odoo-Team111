import * as React from 'react';
import { Link } from 'react-router-dom';
import { Activity, Radio, ShieldAlert } from 'lucide-react';

import { StatusMessage } from '@/components/StatusMessage';
import { Badge } from '@/components/ui/badge';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { useAnomalies } from '@/hooks/useInsights';
import { useRealtimeChannel } from '@/hooks/useRealtime';
import { ANOMALY_LABELS, SEVERITY_VARIANT } from '@/types/insights';

/**
 * Deterministic anomaly detection (PRD §5.7), plus the live payroll feed.
 *
 * EVERY CARD ON THIS SCREEN IS A QUERY RESULT
 * -------------------------------------------
 * There is no static demo data here and no placeholder card. If the seven
 * checks find nothing, the screen says so — an empty state is a real answer,
 * and filling it with a fabricated example would make the one screen whose
 * purpose is "these are real problems in your data" the least trustworthy one
 * in the product.
 *
 * Each finding shows the comparison that produced it (`current_value` against
 * `baseline`) and the threshold it crossed, so a reader can disagree with the
 * threshold rather than having to trust the label.
 */

function firstOfMonth(): string {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), 1).toISOString().slice(0, 10);
}

function lastOfMonth(): string {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth() + 1, 0).toISOString().slice(0, 10);
}

function LiveIndicator() {
  const { state, events } = useRealtimeChannel('payroll');
  const label =
    state === 'live' ? 'Live' : state === 'connecting' ? 'Connecting…' : 'Live feed unavailable';
  const variant = state === 'live' ? 'success' : state === 'connecting' ? 'secondary' : 'warning';

  return (
    <div className="flex items-center gap-2">
      <Radio className={`h-4 w-4 ${state === 'live' ? 'text-emerald-400' : 'text-slate-500'}`} />
      <Badge variant={variant}>{label}</Badge>
      {state === 'unavailable' && (
        <span className="text-xs text-slate-500">
          Figures below are unaffected — they come from the API, not the socket.
        </span>
      )}
      {events.length > 0 && (
        <span className="text-xs text-slate-500">{events.length} live update(s) this session</span>
      )}
    </div>
  );
}

export function AnomaliesPage() {
  const [start, setStart] = React.useState(firstOfMonth);
  const [end, setEnd] = React.useState(lastOfMonth);
  const anomalies = useAnomalies({ period_start: start, period_end: end });

  const summary = anomalies.data?.summary;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Anomalies</h1>
          <p className="mt-1 max-w-2xl text-sm text-slate-400">
            Deterministic checks over real records. These are application queries with stated
            thresholds — not a model's opinion, and never a judgement about whether payroll is
            valid.
          </p>
        </div>
        <LiveIndicator />
      </div>

      <div className="grid gap-3 sm:grid-cols-2 lg:w-1/2">
        <div>
          <label className="text-xs uppercase tracking-wide text-slate-500">Period start</label>
          <Input className="mt-1" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </div>
        <div>
          <label className="text-xs uppercase tracking-wide text-slate-500">Period end</label>
          <Input className="mt-1" type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </div>
      </div>

      {anomalies.isLoading && <p className="text-sm text-slate-400">Running the checks…</p>}
      {anomalies.error && <StatusMessage error={anomalies.error} />}

      {anomalies.data && (
        <>
          <section className="grid gap-px overflow-hidden rounded-md border border-slate-800 bg-slate-800 sm:grid-cols-3">
            <div className="bg-white p-4">
              <p className="text-sm text-slate-400">Findings</p>
              <p className="mt-1 text-2xl font-semibold text-slate-100">{summary?.total ?? 0}</p>
            </div>
            <div className="bg-white p-4">
              <p className="text-sm text-slate-400">By severity</p>
              <div className="mt-2 flex flex-wrap gap-1">
                {Object.entries(summary?.by_severity ?? {}).map(([severity, count]) => (
                  <Badge key={severity} variant={SEVERITY_VARIANT[severity] ?? 'secondary'}>
                    {severity}: {count}
                  </Badge>
                ))}
                {Object.keys(summary?.by_severity ?? {}).length === 0 && (
                  <span className="text-sm text-slate-400">none</span>
                )}
              </div>
            </div>
            <div className="bg-white p-4">
              <p className="text-sm text-slate-400">Checks that fired</p>
              <div className="mt-2 flex flex-wrap gap-1">
                {Object.keys(summary?.by_type ?? {}).map((type) => (
                  <Badge key={type} variant="secondary">
                    {ANOMALY_LABELS[type] ?? type}
                  </Badge>
                ))}
                {Object.keys(summary?.by_type ?? {}).length === 0 && (
                  <span className="text-sm text-slate-400">none</span>
                )}
              </div>
            </div>
          </section>

          {anomalies.data.anomalies.length === 0 ? (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <Activity className="h-4 w-4 text-emerald-400" />
                  Nothing unusual in this period
                </CardTitle>
                <CardDescription>
                  All seven checks ran and none of them fired. This is a real result, not a
                  placeholder.
                </CardDescription>
              </CardHeader>
            </Card>
          ) : (
            <ul className="space-y-2">
              {anomalies.data.anomalies.map((anomaly, index) => (
                <li
                  key={`${anomaly.type}-${anomaly.employee_id ?? anomaly.department ?? index}`}
                  className="rounded-lg border border-slate-800 bg-slate-900/40 p-4"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <ShieldAlert
                        className={`h-4 w-4 ${
                          anomaly.severity === 'high' ? 'text-rose-400' : 'text-amber-400'
                        }`}
                      />
                      <span className="font-medium text-slate-100">
                        {ANOMALY_LABELS[anomaly.type] ?? anomaly.type}
                      </span>
                      <Badge variant={SEVERITY_VARIANT[anomaly.severity] ?? 'secondary'}>
                        {anomaly.severity}
                      </Badge>
                      {anomaly.department && <Badge variant="secondary">{anomaly.department}</Badge>}
                    </div>
                    {anomaly.navigate_to && (
                      <Link
                        to={anomaly.navigate_to}
                        className="text-xs text-indigo-400 underline-offset-4 hover:underline"
                      >
                        Open record
                      </Link>
                    )}
                  </div>

                  <p className="mt-2 text-sm text-slate-200">{anomaly.message}</p>
                  <p className="mt-1 text-xs leading-snug text-slate-500">{anomaly.reason}</p>

                  {(anomaly.current_value || anomaly.baseline) && (
                    <div className="mt-2 flex flex-wrap gap-4 text-xs">
                      {anomaly.current_value && (
                        <span className="text-slate-400">
                          Current:{' '}
                          <span className="font-mono tabular-nums text-slate-200">
                            {anomaly.current_value}
                          </span>
                        </span>
                      )}
                      {anomaly.baseline && (
                        <span className="text-slate-400">
                          Compared with:{' '}
                          <span className="font-mono tabular-nums text-slate-200">
                            {anomaly.baseline}
                          </span>
                        </span>
                      )}
                      {anomaly.period && <span className="text-slate-500">{anomaly.period}</span>}
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Thresholds</CardTitle>
              <CardDescription>
                Configured conventions for this deployment, not payroll rules. A finding means a
                value crossed one of these bounds — nothing more.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <dl className="grid gap-2 text-sm sm:grid-cols-2">
                {Object.entries(anomalies.data.thresholds)
                  .filter(([key]) => key !== 'note')
                  .map(([key, value]) => (
                    <div key={key} className="flex justify-between gap-3">
                      <dt className="text-slate-400">{key.replace(/_/g, ' ')}</dt>
                      <dd className="font-mono tabular-nums text-slate-200">{String(value)}</dd>
                    </div>
                  ))}
              </dl>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
