import * as React from 'react';
import { AlertTriangle, Calculator, Info } from 'lucide-react';

import { StatusMessage } from '@/components/StatusMessage';
import { Badge } from '@/components/ui/badge';
import { usePayComparison, usePayslipCalculation } from '@/hooks/useInsights';
import type { CalculationLine, Delta, LineChange } from '@/types/insights';

/**
 * "View Calculation" (PRD §5.6) and "Why did pay change?" (PRD §5.9).
 *
 * READ-ONLY, AND NOT A CALCULATOR
 * -------------------------------
 * Every figure here is a string the server sent. This component performs no
 * arithmetic at all — not even summing the lines to check the total. The
 * category subtotals it renders were computed server-side from the same
 * persisted rows, and they are shown ALONGSIDE the payslip's own gross and net
 * rather than in place of them, so the two can be compared by eye.
 *
 * That is deliberate. A frontend that re-added the lines would eventually
 * disagree with the payslip — through a float, a rounding rule, or a filter —
 * and the screen whose whole job is "here is where this number came from"
 * would be the one showing a different number.
 */

const CATEGORY_LABELS: Record<string, string> = {
  basic: 'Basic',
  allowance: 'Allowances',
  gross: 'Gross',
  deduction: 'Deductions',
  net: 'Net',
};

function DirectionBadge({ delta }: { delta: Delta | null | undefined }) {
  if (!delta || delta.direction === 'unchanged') {
    return <span className="text-slate-500">unchanged</span>;
  }
  if (delta.direction === 'unknown') {
    return <Badge variant="secondary">not established</Badge>;
  }
  return (
    <Badge variant={delta.direction === 'increase' ? 'success' : 'warning'}>
      {delta.direction === 'increase' ? '+' : ''}
      {delta.delta}
    </Badge>
  );
}

function LineRow({ line }: { line: CalculationLine }) {
  const definition = line.rule_definition_now;
  return (
    <tr className="border-b border-slate-800/70 last:border-0">
      <td className="py-2 pr-3 text-xs text-slate-500 tabular-nums">{line.sequence}</td>
      <td className="py-2 pr-3">
        <div className="font-medium text-slate-200">{line.name}</div>
        <div className="font-mono text-[11px] text-slate-500">{line.code}</div>
        {definition && (
          <div className="mt-1 text-[11px] text-slate-500">
            {definition.computation_method}
            {definition.expression ? (
              <span className="ml-1 font-mono text-slate-400">{definition.expression}</span>
            ) : null}
            {definition.percentage_base_code ? (
              <span className="ml-1 font-mono text-slate-400">
                {definition.amount}% of {definition.percentage_base_code}
              </span>
            ) : null}
            {/* The rule as it is TODAY. If it has been edited since, the line
                above still shows what actually ran, and this says so. */}
            {(line.rule_renamed_since || line.rule_recategorised_since) && (
              <span className="ml-1 text-amber-400">· rule edited since this ran</span>
            )}
          </div>
        )}
        {!line.rule_still_exists && (
          <div className="mt-1 text-[11px] text-slate-500">
            The rule this line came from no longer exists. The line is unaffected.
          </div>
        )}
      </td>
      <td className="py-2 pr-3">
        <Badge variant="secondary">{CATEGORY_LABELS[line.category] ?? line.category}</Badge>
      </td>
      <td className="py-2 text-right font-mono tabular-nums text-slate-100">{line.amount}</td>
    </tr>
  );
}

function ChangeRow({ change }: { change: LineChange }) {
  return (
    <tr className="border-b border-slate-800/70 last:border-0">
      <td className="py-2 pr-3">
        <div className="text-slate-200">{change.name}</div>
        <div className="font-mono text-[11px] text-slate-500">{change.code}</div>
      </td>
      <td className="py-2 pr-3 font-mono tabular-nums text-slate-400">{change.before ?? '—'}</td>
      <td className="py-2 pr-3 font-mono tabular-nums text-slate-200">{change.after ?? '—'}</td>
      <td className="py-2 text-right">
        {change.change === 'changed' ? (
          <Badge variant={change.direction === 'increase' ? 'success' : 'warning'}>
            {change.direction === 'increase' ? '+' : ''}
            {change.delta}
          </Badge>
        ) : (
          <Badge variant="secondary">{change.change}</Badge>
        )}
      </td>
    </tr>
  );
}

export function ViewCalculation({ payslipId }: { payslipId: string }) {
  const [tab, setTab] = React.useState<'calculation' | 'comparison'>('calculation');
  const calculation = usePayslipCalculation(payslipId);
  const comparison = usePayComparison(payslipId, tab === 'comparison');

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 border-b border-slate-800 pb-2">
        <Calculator className="h-4 w-4 text-indigo-400" />
        {(['calculation', 'comparison'] as const).map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`rounded-md px-3 py-1 text-sm transition-colors ${
              tab === key
                ? 'bg-slate-800 text-slate-100'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            {key === 'calculation' ? 'View calculation' : 'Why did pay change?'}
          </button>
        ))}
      </div>

      {tab === 'calculation' && (
        <>
          {calculation.isLoading && <p className="text-sm text-slate-400">Loading calculation…</p>}
          {calculation.error && <StatusMessage error={calculation.error} />}
          {calculation.data && (
            <div className="space-y-4">
              <section>
                <h4 className="text-xs uppercase tracking-wide text-slate-500">
                  Inputs, frozen when this was computed
                </h4>
                {calculation.data.inputs.available ? (
                  <dl className="mt-2 grid gap-2 sm:grid-cols-2">
                    {calculation.data.inputs.values.map((input) => (
                      <div key={input.name} className="rounded-lg border border-slate-800 p-2">
                        <dt className="font-mono text-[11px] text-slate-500">{input.name}</dt>
                        <dd className="font-mono tabular-nums text-slate-100">{input.value}</dd>
                        <p className="mt-1 text-[11px] leading-snug text-slate-500">
                          {input.description}
                        </p>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <div className="mt-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-100">
                    <p className="flex items-center gap-2 font-medium text-amber-200">
                      <AlertTriangle className="h-4 w-4" /> Inputs unavailable
                    </p>
                    <p className="mt-1">{calculation.data.inputs.reason}</p>
                  </div>
                )}
              </section>

              <section>
                <h4 className="text-xs uppercase tracking-wide text-slate-500">
                  Rules, in the order they ran
                </h4>
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-800 text-left text-xs uppercase tracking-wide text-slate-500">
                        <th className="pb-2 pr-3">#</th>
                        <th className="pb-2 pr-3">Rule</th>
                        <th className="pb-2 pr-3">Category</th>
                        <th className="pb-2 text-right">Amount</th>
                      </tr>
                    </thead>
                    <tbody>
                      {calculation.data.lines.map((line) => (
                        <LineRow key={line.code} line={line} />
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>

              <section className="rounded-lg border border-slate-800 p-3">
                <div className="grid gap-2 sm:grid-cols-2">
                  <div>
                    <h4 className="text-xs uppercase tracking-wide text-slate-500">Subtotals</h4>
                    <ul className="mt-1 space-y-0.5 text-sm">
                      {calculation.data.category_subtotals.map((subtotal) => (
                        <li key={subtotal.category} className="flex justify-between gap-4">
                          <span className="text-slate-400">
                            {CATEGORY_LABELS[subtotal.category] ?? subtotal.category}
                          </span>
                          <span className="font-mono tabular-nums text-slate-200">
                            {subtotal.total}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <h4 className="text-xs uppercase tracking-wide text-slate-500">
                      Payslip totals (authoritative)
                    </h4>
                    <ul className="mt-1 space-y-0.5 text-sm">
                      <li className="flex justify-between gap-4">
                        <span className="text-slate-400">Worked days</span>
                        <span className="font-mono tabular-nums text-slate-200">
                          {calculation.data.totals.worked_days}
                        </span>
                      </li>
                      <li className="flex justify-between gap-4">
                        <span className="text-slate-400">Gross</span>
                        <span className="font-mono tabular-nums text-slate-100">
                          {calculation.data.totals.gross_amount}
                        </span>
                      </li>
                      <li className="flex justify-between gap-4">
                        <span className="text-slate-400">Net</span>
                        <span className="font-mono tabular-nums text-emerald-300">
                          {calculation.data.totals.net_amount}
                        </span>
                      </li>
                    </ul>
                  </div>
                </div>
                <p className="mt-3 flex items-start gap-2 text-[11px] leading-snug text-slate-500">
                  <Info className="mt-0.5 h-3 w-3 shrink-0" />
                  {calculation.data.source}
                </p>
              </section>
            </div>
          )}
        </>
      )}

      {tab === 'comparison' && (
        <>
          {comparison.isLoading && <p className="text-sm text-slate-400">Loading comparison…</p>}
          {comparison.error && <StatusMessage error={comparison.error} />}
          {comparison.data && !comparison.data.comparable && (
            <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-sm text-slate-300">
              {comparison.data.reason}
            </div>
          )}
          {comparison.data?.comparable && (
            <div className="space-y-4">
              <section className="grid gap-2 sm:grid-cols-3">
                {(['gross_amount', 'net_amount', 'worked_days'] as const).map((key) => (
                  <div key={key} className="rounded-lg border border-slate-800 p-3">
                    <p className="text-xs uppercase tracking-wide text-slate-500">
                      {key.replace(/_/g, ' ')}
                    </p>
                    <p className="mt-1 font-mono tabular-nums text-slate-100">
                      {comparison.data.totals?.[key]?.before} →{' '}
                      {comparison.data.totals?.[key]?.after}
                    </p>
                    <div className="mt-1 text-sm">
                      <DirectionBadge delta={comparison.data.totals?.[key]} />
                    </div>
                  </div>
                ))}
              </section>

              {comparison.data.input_changes &&
                Object.keys(comparison.data.input_changes).length > 0 && (
                  <section>
                    <h4 className="text-xs uppercase tracking-wide text-slate-500">
                      What changed in the inputs
                    </h4>
                    <ul className="mt-2 space-y-1 text-sm">
                      {Object.entries(comparison.data.input_changes).map(([name, delta]) => (
                        <li
                          key={name}
                          className="flex items-center justify-between gap-3 rounded-lg border border-slate-800 px-3 py-1.5"
                        >
                          <span className="font-mono text-[11px] text-slate-400">{name}</span>
                          <span className="font-mono tabular-nums text-slate-200">
                            {delta.before} → {delta.after}
                          </span>
                          <DirectionBadge delta={delta} />
                        </li>
                      ))}
                    </ul>
                  </section>
                )}

              {comparison.data.line_changes && comparison.data.line_changes.length > 0 ? (
                <section>
                  <h4 className="text-xs uppercase tracking-wide text-slate-500">
                    Rule-by-rule difference
                  </h4>
                  <div className="mt-2 overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-slate-800 text-left text-xs uppercase tracking-wide text-slate-500">
                          <th className="pb-2 pr-3">Rule</th>
                          <th className="pb-2 pr-3">Previous</th>
                          <th className="pb-2 pr-3">Current</th>
                          <th className="pb-2 text-right">Change</th>
                        </tr>
                      </thead>
                      <tbody>
                        {comparison.data.line_changes.map((change) => (
                          <ChangeRow key={change.code} change={change} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                </section>
              ) : (
                <p className="text-sm text-slate-400">
                  No individual rule changed between the two periods.
                </p>
              )}

              {comparison.data.unavailable && comparison.data.unavailable.length > 0 && (
                <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-100">
                  <p className="font-medium text-amber-200">Not established by the data</p>
                  <ul className="mt-1 space-y-0.5">
                    {comparison.data.unavailable.map((note) => (
                      <li key={note}>• {note}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
