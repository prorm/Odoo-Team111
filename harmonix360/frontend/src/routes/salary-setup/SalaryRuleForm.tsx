import * as React from 'react';
import { ShieldCheck } from 'lucide-react';

import { FormField } from '@/components/FormField';
import { StatusMessage } from '@/components/StatusMessage';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { useDeleteSalaryRule, useSaveSalaryRule } from '@/hooks/useSalary';
import { ApiError } from '@/lib/api-client';
import type { SalaryRule, SalaryRuleInput } from '@/types/salary';
import {
  SALARY_CATEGORY_LABELS,
  SALARY_CATEGORY_ORDER,
  SALARY_COMPUTATION_LABELS,
  SalaryRuleCategory,
  SalaryRuleComputation,
} from '@/types/salary';

interface SalaryRuleFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  rule?: SalaryRule;
  canManage: boolean;
}

const EMPTY_RULE: SalaryRuleInput = {
  name: '',
  code: '',
  category: SalaryRuleCategory.BASIC,
  sequence: 100,
  computation_method: SalaryRuleComputation.FIXED,
  amount: '0.00',
  percentage_base_code: null,
  expression: null,
  is_active: true,
  description: null,
};

function ruleToInput(rule: SalaryRule): SalaryRuleInput {
  return {
    name: rule.name,
    code: rule.code,
    category: rule.category,
    sequence: rule.sequence,
    computation_method: rule.computation_method,
    amount: rule.amount,
    percentage_base_code: rule.percentage_base_code,
    expression: rule.expression,
    is_active: rule.is_active,
    description: rule.description,
    version: rule.version,
  };
}

export function SalaryRuleForm({ open, onOpenChange, rule, canManage }: SalaryRuleFormProps) {
  const [values, setValues] = React.useState<SalaryRuleInput>(EMPTY_RULE);
  const [initialSnapshot, setInitialSnapshot] = React.useState('');
  const expressionRef = React.useRef<HTMLTextAreaElement>(null);
  const save = useSaveSalaryRule();
  const remove = useDeleteSalaryRule();
  const isEdit = Boolean(rule);

  React.useEffect(() => {
    if (!open) return;
    const initial = rule ? ruleToInput(rule) : { ...EMPTY_RULE };
    setValues(initial);
    setInitialSnapshot(JSON.stringify(initial));
    save.reset();
    remove.reset();
    // Mutation objects are intentionally omitted: their identity changes as
    // request state changes and would reset a form while somebody is typing.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, rule?.id]);

  function set<K extends keyof SalaryRuleInput>(key: K, value: SalaryRuleInput[K]) {
    setValues((previous) => ({ ...previous, [key]: value }));
  }

  function changeMethod(method: SalaryRuleComputation) {
    setValues((previous) => ({
      ...previous,
      computation_method: method,
      amount: method === SalaryRuleComputation.FORMULA ? null : previous.amount ?? '0.00',
      percentage_base_code:
        method === SalaryRuleComputation.PERCENTAGE ? previous.percentage_base_code ?? '' : null,
      expression: method === SalaryRuleComputation.FORMULA ? previous.expression ?? '' : null,
    }));
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    try {
      await save.mutateAsync({ id: rule?.id, values });
      onOpenChange(false);
    } catch {
      // StatusMessage renders the server's authoritative validation response.
    }
  }

  async function handleDelete() {
    if (!rule) return;
    const confirmed = window.confirm(
      `Remove ${rule.code}? Existing payslip history is preserved, but the rule will no longer be available for new structures.`,
    );
    if (!confirmed) return;
    try {
      await remove.mutateAsync(rule.id);
      onOpenChange(false);
    } catch {
      // StatusMessage keeps the failure attached to this action.
    }
  }

  const pending = save.isPending || remove.isPending;
  const readOnly = isEdit && !canManage;
  const dirty = canManage && JSON.stringify(values) !== initialSnapshot;
  const formulaError =
    values.computation_method === SalaryRuleComputation.FORMULA &&
    save.error instanceof ApiError &&
    save.error.status === 422
      ? save.error.message
          .split('; ')
          .find((message) => message.startsWith('expression:')) ?? null
      : null;

  React.useEffect(() => {
    if (!formulaError) return;
    expressionRef.current?.focus();
    expressionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, [formulaError]);

  function requestOpenChange(nextOpen: boolean) {
    if (
      !nextOpen &&
      dirty &&
      !pending &&
      !window.confirm('Discard your unsaved salary rule changes?')
    ) {
      return;
    }
    onOpenChange(nextOpen);
  }

  return (
    <Dialog open={open} onOpenChange={requestOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {readOnly ? rule?.name : isEdit ? `Edit ${rule?.name}` : 'New salary rule'}
          </DialogTitle>
          {readOnly && (
            <p className="text-xs text-slate-400">Your payroll role has read-only access to salary configuration.</p>
          )}
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-5">
          <StatusMessage error={save.error ?? remove.error} />

          <div className="grid gap-4 sm:grid-cols-2">
            <FormField label="Rule name" htmlFor="salary_rule_name" required>
              <Input
                id="salary_rule_name"
                value={values.name}
                onChange={(event) => set('name', event.target.value)}
                disabled={readOnly}
                required
              />
            </FormField>
            <FormField
              label="Code"
              htmlFor="salary_rule_code"
              required
              hint="Letters, digits, and underscores; formulas reference this code."
            >
              <Input
                id="salary_rule_code"
                value={values.code}
                onChange={(event) => set('code', event.target.value)}
                disabled={readOnly}
                className="font-mono"
                required
              />
            </FormField>
            <FormField label="Category" htmlFor="salary_rule_category">
              <Select
                id="salary_rule_category"
                value={values.category}
                onChange={(event) => set('category', event.target.value as SalaryRuleCategory)}
                disabled={readOnly}
              >
                {SALARY_CATEGORY_ORDER.map((category) => (
                  <option key={category} value={category}>
                    {SALARY_CATEGORY_LABELS[category]}
                  </option>
                ))}
              </Select>
            </FormField>
            <FormField
              label="Default sequence"
              htmlFor="salary_rule_sequence"
              hint="Structures may override this execution position."
            >
              <Input
                id="salary_rule_sequence"
                type="number"
                min={0}
                value={values.sequence}
                onChange={(event) => set('sequence', Number(event.target.value))}
                disabled={readOnly}
              />
            </FormField>
            <FormField label="Computation" htmlFor="salary_rule_method" className="sm:col-span-2">
              <Select
                id="salary_rule_method"
                value={values.computation_method}
                onChange={(event) => changeMethod(event.target.value as SalaryRuleComputation)}
                disabled={readOnly}
              >
                {Object.values(SalaryRuleComputation).map((method) => (
                  <option key={method} value={method}>
                    {SALARY_COMPUTATION_LABELS[method]}
                  </option>
                ))}
              </Select>
            </FormField>
          </div>

          {values.computation_method === SalaryRuleComputation.FIXED && (
            <FormField label="Amount" htmlFor="salary_rule_amount" required>
              <Input
                id="salary_rule_amount"
                type="number"
                step="0.01"
                value={values.amount ?? ''}
                onChange={(event) => set('amount', event.target.value)}
                disabled={readOnly}
                required
              />
            </FormField>
          )}

          {values.computation_method === SalaryRuleComputation.PERCENTAGE && (
            <div className="grid gap-4 sm:grid-cols-2">
              <FormField label="Percentage" htmlFor="salary_rule_percentage" required>
                <Input
                  id="salary_rule_percentage"
                  type="number"
                  step="0.01"
                  value={values.amount ?? ''}
                  onChange={(event) => set('amount', event.target.value)}
                  disabled={readOnly}
                  required
                />
              </FormField>
              <FormField
                label="Base rule code"
                htmlFor="salary_rule_base"
                required
                hint="Must be an earlier rule in every linked structure."
              >
                <Input
                  id="salary_rule_base"
                  value={values.percentage_base_code ?? ''}
                  onChange={(event) => set('percentage_base_code', event.target.value)}
                  disabled={readOnly}
                  className="font-mono"
                  required
                />
              </FormField>
            </div>
          )}

          {values.computation_method === SalaryRuleComputation.FORMULA && (
            <FormField
              label="Arithmetic expression"
              htmlFor="salary_rule_expression"
              required
              error={formulaError}
              hint="Numbers, named inputs, and arithmetic operators only. Calls, imports, attributes, and indexing are refused by the server at save time."
            >
              <textarea
                ref={expressionRef}
                id="salary_rule_expression"
                value={values.expression ?? ''}
                onChange={(event) => set('expression', event.target.value)}
                disabled={readOnly}
                required
                spellCheck={false}
                rows={4}
                aria-invalid={Boolean(formulaError)}
                aria-describedby={
                  formulaError ? 'salary_rule_expression-error' : 'salary_rule_expression-hint'
                }
                className="w-full resize-y rounded-lg border border-slate-800 bg-slate-950/80 px-3 py-2 font-mono text-sm text-slate-100 placeholder:text-slate-400 focus-visible:border-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
              />
            </FormField>
          )}

          <FormField label="Description" htmlFor="salary_rule_description">
            <textarea
              id="salary_rule_description"
              value={values.description ?? ''}
              onChange={(event) => set('description', event.target.value || null)}
              disabled={readOnly}
              rows={2}
              className="w-full resize-y rounded-lg border border-slate-800 bg-slate-950/80 px-3 py-2 text-sm text-slate-100 focus-visible:border-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
            />
          </FormField>

          <label className="flex items-center gap-2 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={values.is_active}
              onChange={(event) => set('is_active', event.target.checked)}
              disabled={readOnly}
              className="h-4 w-4 rounded border-slate-700 bg-slate-950 text-indigo-600 focus:ring-indigo-500"
            />
            Active and available for salary structures
          </label>

          {values.computation_method === SalaryRuleComputation.FORMULA && (
            <div className="flex gap-2 rounded-lg bg-slate-950/60 px-3 py-2.5 text-xs text-slate-400">
              <ShieldCheck className="mt-0.5 h-4 w-4 flex-none text-emerald-400" aria-hidden="true" />
              <p>The expression is parsed as restricted arithmetic. It is never executed as Python code.</p>
            </div>
          )}

          <DialogFooter className="sm:justify-between">
            <div>
              {canManage && isEdit && (
                <Button type="button" variant="destructive" onClick={handleDelete} disabled={pending}>
                  Remove rule
                </Button>
              )}
            </div>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => requestOpenChange(false)}>
                {readOnly ? 'Close' : 'Cancel'}
              </Button>
              {!readOnly && (
                <Button type="submit" disabled={pending}>
                  {save.isPending ? 'Saving…' : isEdit ? 'Save rule' : 'Create rule'}
                </Button>
              )}
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
