import * as React from 'react';
import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react';

import { FormField } from '@/components/FormField';
import { StatusMessage } from '@/components/StatusMessage';
import { Badge } from '@/components/ui/badge';
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
import { useDeleteSalaryStructure, useSaveSalaryStructure } from '@/hooks/useSalary';
import type { SalaryRule, SalaryStructure, SalaryStructureInput } from '@/types/salary';
import { SALARY_CATEGORY_LABELS } from '@/types/salary';

interface SalaryStructureFormProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  structure?: SalaryStructure;
  rules: SalaryRule[];
  canManage: boolean;
}

interface OrderedRule {
  id: string;
  name: string;
  code: string;
  category: SalaryRule['category'];
  is_active: boolean;
}

export function SalaryStructureForm({
  open,
  onOpenChange,
  structure,
  rules,
  canManage,
}: SalaryStructureFormProps) {
  const [name, setName] = React.useState('');
  const [code, setCode] = React.useState('');
  const [description, setDescription] = React.useState('');
  const [isActive, setIsActive] = React.useState(true);
  const [orderedRules, setOrderedRules] = React.useState<OrderedRule[]>([]);
  const [ruleToAdd, setRuleToAdd] = React.useState('');
  const [initialSnapshot, setInitialSnapshot] = React.useState('');
  const save = useSaveSalaryStructure();
  const remove = useDeleteSalaryStructure();
  const isEdit = Boolean(structure);
  const readOnly = isEdit && !canManage;

  React.useEffect(() => {
    if (!open) return;
    const initialName = structure?.name ?? '';
    const initialCode = structure?.code ?? '';
    const initialDescription = structure?.description ?? '';
    const initialActive = structure?.is_active ?? true;
    const initialRules =
      [...(structure?.rules ?? [])]
        .sort((a, b) => a.sequence - b.sequence)
        .map((link) => ({
          id: link.salary_rule.id,
          name: link.salary_rule.name,
          code: link.salary_rule.code,
          category: link.salary_rule.category,
          is_active: link.salary_rule.is_active,
        }));
    setName(initialName);
    setCode(initialCode);
    setDescription(initialDescription);
    setIsActive(initialActive);
    setOrderedRules(initialRules);
    setInitialSnapshot(
      JSON.stringify({
        name: initialName,
        code: initialCode,
        description: initialDescription,
        isActive: initialActive,
        ruleIds: initialRules.map((rule) => rule.id),
      }),
    );
    setRuleToAdd('');
    save.reset();
    remove.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, structure?.id]);

  const availableRules = rules.filter(
    (rule) => rule.is_active && !orderedRules.some((selected) => selected.id === rule.id),
  );

  function moveRule(index: number, direction: -1 | 1) {
    const nextIndex = index + direction;
    if (nextIndex < 0 || nextIndex >= orderedRules.length) return;
    setOrderedRules((previous) => {
      const next = [...previous];
      [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
      return next;
    });
  }

  function addRule() {
    const rule = rules.find((candidate) => candidate.id === ruleToAdd);
    if (!rule) return;
    setOrderedRules((previous) => [
      ...previous,
      {
        id: rule.id,
        name: rule.name,
        code: rule.code,
        category: rule.category,
        is_active: rule.is_active,
      },
    ]);
    setRuleToAdd('');
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    const values: SalaryStructureInput = {
      name,
      code,
      is_active: isActive,
      description: description || null,
      rules: orderedRules.map((rule, index) => ({
        salary_rule_id: rule.id,
        sequence: (index + 1) * 10,
      })),
    };
    try {
      await save.mutateAsync({ id: structure?.id, values });
      onOpenChange(false);
    } catch {
      // The structure-order validation message remains visible in this dialog.
    }
  }

  async function handleDelete() {
    if (!structure) return;
    const confirmed = window.confirm(
      `Remove ${structure.code}? It is currently referenced by ${structure.contract_usage_count} contract${structure.contract_usage_count === 1 ? '' : 's'}. Existing payroll history is preserved.`,
    );
    if (!confirmed) return;
    try {
      await remove.mutateAsync(structure.id);
      onOpenChange(false);
    } catch {
      // StatusMessage renders the server response.
    }
  }

  const pending = save.isPending || remove.isPending;
  const dirty =
    canManage &&
    JSON.stringify({
      name,
      code,
      description,
      isActive,
      ruleIds: orderedRules.map((rule) => rule.id),
    }) !== initialSnapshot;

  function requestOpenChange(nextOpen: boolean) {
    if (
      !nextOpen &&
      dirty &&
      !pending &&
      !window.confirm('Discard your unsaved salary structure changes?')
    ) {
      return;
    }
    onOpenChange(nextOpen);
  }

  return (
    <Dialog open={open} onOpenChange={requestOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {readOnly ? structure?.name : isEdit ? `Edit ${structure?.name}` : 'New salary structure'}
          </DialogTitle>
          {structure && (
            <p className="text-xs text-slate-400">
              {structure.rule_count} rule{structure.rule_count === 1 ? '' : 's'} · used by{' '}
              {structure.contract_usage_count} contract{structure.contract_usage_count === 1 ? '' : 's'}
            </p>
          )}
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-5">
          <StatusMessage error={save.error ?? remove.error} />

          <div className="grid gap-4 sm:grid-cols-2">
            <FormField label="Structure name" htmlFor="salary_structure_name" required>
              <Input
                id="salary_structure_name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                disabled={readOnly}
                required
              />
            </FormField>
            <FormField label="Code" htmlFor="salary_structure_code" required>
              <Input
                id="salary_structure_code"
                value={code}
                onChange={(event) => setCode(event.target.value)}
                disabled={readOnly}
                className="font-mono"
                required
              />
            </FormField>
          </div>

          <FormField label="Description" htmlFor="salary_structure_description">
            <textarea
              id="salary_structure_description"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              disabled={readOnly}
              rows={2}
              className="w-full resize-y rounded-lg border border-slate-800 bg-slate-950/80 px-3 py-2 text-sm text-slate-100 focus-visible:border-indigo-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
            />
          </FormField>

          <section className="space-y-3">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <div>
                <h3 className="text-sm font-medium text-slate-200">Execution sequence</h3>
                <p className="mt-0.5 text-xs text-slate-400">
                  Rules run top to bottom. Formula dependencies must appear earlier.
                </p>
              </div>
              {!readOnly && (
                <div className="flex min-w-0 flex-1 gap-2 sm:max-w-sm">
                  <Select
                    value={ruleToAdd}
                    onChange={(event) => setRuleToAdd(event.target.value)}
                    aria-label="Rule to add"
                    className="h-9"
                  >
                    <option value="">Select a rule</option>
                    {availableRules.map((rule) => (
                      <option key={rule.id} value={rule.id}>
                        {rule.code} — {rule.name}
                      </option>
                    ))}
                  </Select>
                  <Button type="button" variant="outline" size="sm" onClick={addRule} disabled={!ruleToAdd}>
                    <Plus className="mr-1.5 h-3.5 w-3.5" />
                    Add
                  </Button>
                </div>
              )}
            </div>

            {orderedRules.length === 0 ? (
              <p className="rounded-lg border border-dashed border-slate-800 px-4 py-8 text-center text-xs text-slate-400">
                No rules in this structure yet.
              </p>
            ) : (
              <ol className="overflow-hidden rounded-md border border-slate-800">
                {orderedRules.map((rule, index) => (
                  <li
                    key={rule.id}
                    className="flex items-center gap-3 border-b border-slate-800 bg-slate-950/40 px-3 py-3 last:border-b-0"
                  >
                    <span className="flex h-6 w-6 flex-none items-center justify-center rounded-full bg-slate-800 text-[11px] font-semibold text-slate-300">
                      {index + 1}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-slate-200">{rule.name}</p>
                      <p className="mt-0.5 truncate font-mono text-[11px] text-slate-400">{rule.code}</p>
                    </div>
                    <Badge variant="secondary" className="hidden text-[10px] sm:inline-flex">
                      {SALARY_CATEGORY_LABELS[rule.category]}
                    </Badge>
                    {!rule.is_active && <Badge variant="warning">Inactive</Badge>}
                    {!readOnly && (
                      <div className="flex items-center gap-0.5">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-11 w-11"
                          onClick={() => moveRule(index, -1)}
                          disabled={index === 0}
                          aria-label={`Move ${rule.name} earlier`}
                        >
                          <ArrowUp className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-11 w-11"
                          onClick={() => moveRule(index, 1)}
                          disabled={index === orderedRules.length - 1}
                          aria-label={`Move ${rule.name} later`}
                        >
                          <ArrowDown className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-11 w-11"
                          onClick={() => setOrderedRules((previous) => previous.filter((item) => item.id !== rule.id))}
                          aria-label={`Remove ${rule.name} from structure`}
                        >
                          <Trash2 className="h-3.5 w-3.5 text-rose-400" />
                        </Button>
                      </div>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </section>

          <label className="flex items-center gap-2 text-xs text-slate-300">
            <input
              type="checkbox"
              checked={isActive}
              onChange={(event) => setIsActive(event.target.checked)}
              disabled={readOnly}
              className="h-4 w-4 rounded border-slate-700 bg-slate-950 text-indigo-600 focus:ring-indigo-500"
            />
            Active and available for contracts and new payruns
          </label>

          <DialogFooter className="sm:justify-between">
            <div>
              {canManage && isEdit && (
                <Button type="button" variant="destructive" onClick={handleDelete} disabled={pending}>
                  Remove structure
                </Button>
              )}
            </div>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" onClick={() => requestOpenChange(false)}>
                {readOnly ? 'Close' : 'Cancel'}
              </Button>
              {!readOnly && (
                <Button type="submit" disabled={pending}>
                  {save.isPending ? 'Saving…' : isEdit ? 'Save structure' : 'Create structure'}
                </Button>
              )}
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
