import { useEffect, useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { useCreatePayrun, useEligibleEmployees, useSalaryStructures } from '@/hooks/usePayroll';
import { formatMoney } from '@/types/payroll';
import type { Payrun } from '@/types/payroll';

import { ErrorMessage, Field } from '../hr-shared';

/**
 * PS B5's wizard: Step 1 (Structure + Period) → Step 2 (explicit employee
 * selection) → Create Payrun.
 *
 * Two decisions in here are deliberate rather than cosmetic:
 *
 *  * **Step 2 offers only ELIGIBLE employees** — people with exactly one
 *    active contract covering the chosen period, which is the same question
 *    Compute asks. Listing everyone and letting Compute skip half of them
 *    would move the disappointment to the least recoverable moment.
 *
 *  * **Nothing is pre-selected.** PS B5 calls the selection explicit, and a
 *    "select all" default is how somebody gets paid in a run nobody chose to
 *    include them in. Select-all is available as an action; it is just not
 *    the starting state.
 */
export function PayrunWizard({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (payrun: Payrun) => void;
}) {
  const [step, setStep] = useState<1 | 2>(1);
  const [name, setName] = useState('');
  const [structureId, setStructureId] = useState('');
  const [periodStart, setPeriodStart] = useState('');
  const [periodEnd, setPeriodEnd] = useState('');
  const [selected, setSelected] = useState<string[]>([]);

  const structures = useSalaryStructures();
  const eligible = useEligibleEmployees(periodStart, periodEnd);
  const create = useCreatePayrun();

  // Reopening the wizard must not resume a half-finished previous run.
  useEffect(() => {
    if (!open) {
      setStep(1);
      setName('');
      setStructureId('');
      setPeriodStart('');
      setPeriodEnd('');
      setSelected([]);
      create.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const periodValid = Boolean(periodStart && periodEnd && periodEnd >= periodStart);
  const stepOneComplete = Boolean(name.trim() && structureId && periodValid);
  const candidates = eligible.data?.items ?? [];

  const partialCount = useMemo(
    () => candidates.filter((row) => selected.includes(row.employee.id) && row.partial_period).length,
    [candidates, selected]
  );

  function toggle(employeeId: string) {
    setSelected((current) =>
      current.includes(employeeId)
        ? current.filter((id) => id !== employeeId)
        : [...current, employeeId]
    );
  }

  async function submit() {
    const payrun = await create.mutateAsync({
      name: name.trim(),
      salary_structure_id: structureId,
      period_start: periodStart,
      period_end: periodEnd,
      employee_ids: selected,
    });
    onCreated(payrun);
    onOpenChange(false);
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogTitle>New payrun — step {step} of 2</DialogTitle>
        <DialogDescription>
          {step === 1
            ? 'Choose the salary structure this run executes and the period it covers.'
            : 'Select the employees to include. Only employees with one active contract covering the period are listed.'}
        </DialogDescription>

        {step === 1 ? (
          <div className="space-y-4">
            <Field label="Payrun name">
              <Input
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="March 2025 — monthly staff"
              />
            </Field>
            <Field label="Salary structure">
              <Select value={structureId} onChange={(event) => setStructureId(event.target.value)}>
                <option value="">Select a structure</option>
                {structures.data?.items.map((structure) => (
                  <option key={structure.id} value={structure.id}>
                    {structure.name} ({structure.code}) — {structure.rule_count} rule
                    {structure.rule_count === 1 ? '' : 's'}
                  </option>
                ))}
              </Select>
            </Field>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Period start">
                <Input
                  type="date"
                  value={periodStart}
                  onChange={(event) => setPeriodStart(event.target.value)}
                />
              </Field>
              <Field label="Period end">
                <Input
                  type="date"
                  value={periodEnd}
                  onChange={(event) => setPeriodEnd(event.target.value)}
                />
              </Field>
            </div>
            {periodStart && periodEnd && !periodValid && (
              <p role="alert" className="text-sm text-rose-300">
                The period must end on or after it starts.
              </p>
            )}
            <ErrorMessage error={structures.error} />
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center justify-between text-sm text-slate-400">
              <span>
                {selected.length} of {candidates.length} selected
              </span>
              <div className="flex gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setSelected(candidates.map((row) => row.employee.id))}
                >
                  Select all
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setSelected([])}>
                  Clear
                </Button>
              </div>
            </div>

            <div className="max-h-72 overflow-y-auto rounded-lg border border-slate-800">
              {eligible.isLoading && <p className="p-4 text-sm text-slate-400">Loading candidates…</p>}
              {!eligible.isLoading && candidates.length === 0 && (
                <p className="p-4 text-sm text-slate-400">
                  No employee has an active contract covering this period. Create or activate a
                  contract first — payroll resolves exactly one contract per period.
                </p>
              )}
              {candidates.map((row) => (
                <label
                  key={row.employee.id}
                  className="flex cursor-pointer items-center gap-3 border-b border-slate-800/60 p-3 text-sm last:border-0 hover:bg-slate-800/40"
                >
                  <input
                    type="checkbox"
                    className="h-4 w-4 accent-indigo-500"
                    checked={selected.includes(row.employee.id)}
                    onChange={() => toggle(row.employee.id)}
                  />
                  <span className="flex-1 text-slate-200">
                    {row.employee.first_name} {row.employee.last_name}
                    <span className="ml-2 text-slate-500">{row.employee.work_email}</span>
                  </span>
                  <span className="text-slate-400">{formatMoney(row.wage)}</span>
                  {row.partial_period && (
                    <span className="rounded-sm border border-amber-800 bg-amber-950 px-2 py-0.5 text-xs text-amber-400">
                      Partial period
                    </span>
                  )}
                </label>
              ))}
            </div>

            {partialCount > 0 && (
              <p className="text-xs text-amber-300">
                {partialCount} selected employee{partialCount === 1 ? '' : 's'} ha
                {partialCount === 1 ? 's' : 've'} a contract that does not span the whole period.
                They can still be computed; each will carry a blocking “contract gap” warning until
                a human confirms it.
              </p>
            )}
            <ErrorMessage error={eligible.error ?? create.error} />
          </div>
        )}

        <DialogFooter>
          {step === 2 && (
            <Button variant="outline" onClick={() => setStep(1)}>
              Back
            </Button>
          )}
          {step === 1 ? (
            <Button disabled={!stepOneComplete} onClick={() => setStep(2)}>
              Next: select employees
            </Button>
          ) : (
            <Button disabled={selected.length === 0 || create.isPending} onClick={submit}>
              {create.isPending ? 'Creating…' : `Create payrun (${selected.length})`}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
