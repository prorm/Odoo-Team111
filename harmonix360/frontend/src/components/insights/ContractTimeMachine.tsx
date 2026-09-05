import * as React from 'react';
import { History, MoveRight } from 'lucide-react';

import { StatusMessage } from '@/components/StatusMessage';
import { Badge } from '@/components/ui/badge';
import { Input } from '@/components/ui/input';
import { useContractTimeline } from '@/hooks/useInsights';
import type { TimelineContract } from '@/types/insights';

/**
 * Contract "Time Machine" (PRD §5.8).
 *
 * A read view over `Contract` date ranges. Picking a payroll period highlights
 * the contract that period ACTUALLY resolves to — resolved server-side by the
 * payroll engine's own `resolve_period_contract`, not by a date comparison
 * written here.
 *
 * That matters more than it looks. A client-side "which contract covers this
 * date" would agree with the engine almost always, and the exception would be
 * a screen confidently showing a contract the employee was not paid under —
 * on the one screen whose entire purpose is making period-specific contract
 * resolution obvious.
 */
function ContractCard({
  contract,
  highlighted,
}: {
  contract: TimelineContract;
  highlighted: boolean;
}) {
  return (
    <div
      className={`rounded-lg border p-3 transition-colors ${
        highlighted
          ? 'border-indigo-500/60 bg-indigo-500/10'
          : 'border-slate-800 bg-slate-900/40'
      }`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="font-mono tabular-nums text-slate-100">{contract.wage}</span>
          <Badge variant={contract.status === 'active' ? 'success' : 'secondary'}>
            {contract.status}
          </Badge>
          {highlighted && <Badge>applies to this period</Badge>}
        </div>
        <span className="font-mono text-[11px] text-slate-500">{contract.contract_id}</span>
      </div>
      <p className="mt-1 text-sm text-slate-300">
        {contract.start_date} → {contract.end_date ?? 'open-ended'}
      </p>
      <p className="mt-0.5 text-xs text-slate-500">
        {contract.job_position ?? 'No job position'}
        {contract.salary_structure ? ` · ${contract.salary_structure.name}` : ''}
        {contract.working_schedule ? ` · ${contract.working_schedule}` : ''}
      </p>
    </div>
  );
}

export function ContractTimeMachine({ employeeId }: { employeeId: string }) {
  const [start, setStart] = React.useState('');
  const [end, setEnd] = React.useState('');
  const bothSet = Boolean(start && end);

  const timeline = useContractTimeline(
    employeeId,
    bothSet ? { period_start: start, period_end: end } : undefined,
  );

  if (timeline.isLoading) return <p className="text-sm text-slate-400">Loading contract history…</p>;
  if (timeline.error) return <StatusMessage error={timeline.error} />;
  if (!timeline.data) return null;

  const data = timeline.data;
  const resolvedId = data.resolution?.contract?.contract_id;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <History className="h-4 w-4 text-indigo-400" />
        <h3 className="font-medium text-slate-100">Contract time machine</h3>
      </div>

      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <label className="text-xs uppercase tracking-wide text-slate-500">Period start</label>
          <Input className="mt-1" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </div>
        <div>
          <label className="text-xs uppercase tracking-wide text-slate-500">Period end</label>
          <Input className="mt-1" type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </div>
      </div>

      {bothSet && data.resolution && (
        <div
          className={`rounded-lg border p-3 text-sm ${
            data.resolution.resolved
              ? 'border-indigo-500/40 bg-indigo-500/5 text-slate-200'
              : 'border-amber-500/40 bg-amber-500/10 text-amber-100'
          }`}
        >
          {data.resolution.resolved ? (
            <>
              <p>
                This period resolves to contract{' '}
                <span className="font-mono">{data.resolution.contract?.contract_id}</span> at wage{' '}
                <span className="font-mono tabular-nums">{data.resolution.contract?.wage}</span>.
              </p>
              {data.resolution.covers_whole_period === false && (
                <p className="mt-1 text-amber-200">
                  The contract does not cover the whole period. Pay is prorated by worked days,
                  not by contract coverage.
                </p>
              )}
            </>
          ) : (
            <p>{data.resolution.reason}</p>
          )}
        </div>
      )}

      {data.contract_count === 0 ? (
        <p className="text-sm text-slate-400">This employee has no contracts on record.</p>
      ) : (
        <div className="space-y-2">
          {data.contracts.map((contract) => (
            <ContractCard
              key={contract.contract_id}
              contract={contract}
              highlighted={contract.contract_id === resolvedId}
            />
          ))}
        </div>
      )}

      {data.has_wage_change ? (
        <section>
          <h4 className="text-xs uppercase tracking-wide text-slate-500">Wage changes</h4>
          <ul className="mt-2 space-y-1">
            {data.wage_changes.map((change) => (
              <li
                key={`${change.from_contract_id}-${change.to_contract_id}`}
                className="flex flex-wrap items-center gap-2 rounded-lg border border-slate-800 px-3 py-2 text-sm"
              >
                <span className="text-slate-500">{change.effective_date}</span>
                <span className="font-mono tabular-nums text-slate-400">{change.wage_before}</span>
                <MoveRight className="h-3 w-3 text-slate-600" />
                <span className="font-mono tabular-nums text-slate-100">{change.wage_after}</span>
                <Badge variant={change.direction === 'increase' ? 'success' : 'warning'}>
                  {change.wage_delta}
                  {change.percent_change ? ` (${change.percent_change}%)` : ''}
                </Badge>
                {change.other_changed_fields.length > 0 && (
                  <span className="text-xs text-slate-500">
                    also changed: {change.other_changed_fields.join(', ')}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </section>
      ) : (
        data.contract_count > 1 && (
          <p className="text-sm text-slate-400">
            No wage changed between these contracts.
          </p>
        )
      )}
    </div>
  );
}
