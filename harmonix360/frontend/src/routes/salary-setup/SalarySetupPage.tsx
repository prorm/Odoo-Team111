import * as React from 'react';
import {
  Calculator,
  CheckCircle2,
  ArrowRight,
  Layers3,
  Plus,
  Search,
  ShieldCheck,
} from 'lucide-react';

import { StatusMessage } from '@/components/StatusMessage';
import { Badge, type BadgeProps } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useCurrentUser } from '@/hooks/useCurrentUser';
import { useSalaryRules, useSalaryStructuresAdmin } from '@/hooks/useSalary';
import { hasRole, PAYROLL_ADMIN_ROLES } from '@/types/enums';
import type { SalaryRule, SalaryStructure } from '@/types/salary';
import {
  SALARY_CATEGORY_LABELS,
  SALARY_CATEGORY_ORDER,
  SALARY_COMPUTATION_LABELS,
  SalaryRuleCategory,
  SalaryRuleComputation,
} from '@/types/salary';

import { SalaryRuleForm } from './SalaryRuleForm';
import { SalaryStructureForm } from './SalaryStructureForm';

type SetupTab = 'rules' | 'structures';

const CATEGORY_BADGES: Record<SalaryRuleCategory, BadgeProps['variant']> = {
  [SalaryRuleCategory.BASIC]: 'info',
  [SalaryRuleCategory.ALLOWANCE]: 'success',
  [SalaryRuleCategory.GROSS]: 'default',
  [SalaryRuleCategory.DEDUCTION]: 'warning',
  [SalaryRuleCategory.NET]: 'secondary',
};

/** Salary Structure & Rule Setup (PS A5/A6). */
export function SalarySetupPage() {
  const [tab, setTab] = React.useState<SetupTab>('rules');
  const [search, setSearch] = React.useState('');
  const [selectedRule, setSelectedRule] = React.useState<SalaryRule | undefined>();
  const [selectedStructure, setSelectedStructure] = React.useState<SalaryStructure | undefined>();
  const [ruleFormOpen, setRuleFormOpen] = React.useState(false);
  const [structureFormOpen, setStructureFormOpen] = React.useState(false);
  const currentUser = useCurrentUser();
  const rulesQuery = useSalaryRules();
  const structuresQuery = useSalaryStructuresAdmin();

  const rules = rulesQuery.data?.items ?? [];
  const structures = structuresQuery.data?.items ?? [];
  const canManage = hasRole(currentUser.data?.role, PAYROLL_ADMIN_ROLES);
  const normalizedSearch = search.trim().toLocaleLowerCase();
  const filteredRules = rules.filter((rule) =>
    [rule.name, rule.code, SALARY_CATEGORY_LABELS[rule.category]]
      .join(' ')
      .toLocaleLowerCase()
      .includes(normalizedSearch),
  );
  const filteredStructures = structures.filter((structure) =>
    [structure.name, structure.code, structure.description ?? '']
      .join(' ')
      .toLocaleLowerCase()
      .includes(normalizedSearch),
  );

  function openRule(rule?: SalaryRule) {
    setSelectedRule(rule);
    setRuleFormOpen(true);
  }

  function openStructure(structure?: SalaryStructure) {
    setSelectedStructure(structure);
    setStructureFormOpen(true);
  }

  function handleTabKeyDown(event: React.KeyboardEvent<HTMLButtonElement>) {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    setTab((current) => (current === 'rules' ? 'structures' : 'rules'));
    const nextId = tab === 'rules' ? 'salary-structures-tab' : 'salary-rules-tab';
    window.requestAnimationFrame(() => document.getElementById(nextId)?.focus());
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-2xl">
          <h1 className="text-xl font-semibold text-slate-100">Salary setup</h1>
          <p className="mt-1 text-xs leading-relaxed text-slate-400">
            Define deterministic pay rules, then arrange them in the exact order each salary
            structure executes.
          </p>
        </div>
        {canManage && (
          <Button size="sm" onClick={() => (tab === 'rules' ? openRule() : openStructure())}>
            <Plus className="mr-1.5 h-3.5 w-3.5" />
            {tab === 'rules' ? 'New rule' : 'New structure'}
          </Button>
        )}
      </header>

      {!canManage && !currentUser.isLoading && (
        <div className="flex gap-2.5 rounded-lg border border-slate-800 bg-slate-900/60 px-3 py-2.5 text-xs text-slate-300">
          <ShieldCheck className="mt-0.5 h-4 w-4 flex-none text-indigo-400" aria-hidden="true" />
          <p>
            You can inspect salary configuration. Only an HR Payroll Manager can create or change it.
          </p>
        </div>
      )}

      <div className="flex flex-col gap-3 border-b border-slate-800 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex gap-6" role="tablist" aria-label="Salary setup sections">
          <button
            id="salary-rules-tab"
            type="button"
            role="tab"
            aria-selected={tab === 'rules'}
            aria-controls="salary-rules-panel"
            tabIndex={tab === 'rules' ? 0 : -1}
            onClick={() => setTab('rules')}
            onKeyDown={handleTabKeyDown}
            className={`flex items-center gap-2 border-b-2 px-0.5 pb-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
              tab === 'rules'
                ? 'border-indigo-500 text-slate-100'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Calculator className="h-4 w-4" />
            Salary rules
            <span className="text-xs text-slate-400">{rules.length}</span>
          </button>
          <button
            id="salary-structures-tab"
            type="button"
            role="tab"
            aria-selected={tab === 'structures'}
            aria-controls="salary-structures-panel"
            tabIndex={tab === 'structures' ? 0 : -1}
            onClick={() => setTab('structures')}
            onKeyDown={handleTabKeyDown}
            className={`flex items-center gap-2 border-b-2 px-0.5 pb-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 ${
              tab === 'structures'
                ? 'border-indigo-500 text-slate-100'
                : 'border-transparent text-slate-400 hover:text-slate-200'
            }`}
          >
            <Layers3 className="h-4 w-4" />
            Salary structures
            <span className="text-xs text-slate-400">{structures.length}</span>
          </button>
        </div>

        <div className="relative mb-3 w-full sm:w-64">
          <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-500" aria-hidden="true" />
          <Input
            type="search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={tab === 'rules' ? 'Search rules…' : 'Search structures…'}
            aria-label={tab === 'rules' ? 'Search salary rules' : 'Search salary structures'}
            className="h-9 pl-9 text-xs"
          />
        </div>
      </div>

      <StatusMessage error={rulesQuery.error ?? structuresQuery.error} />

      <div
        id={tab === 'rules' ? 'salary-rules-panel' : 'salary-structures-panel'}
        role="tabpanel"
        aria-labelledby={tab === 'rules' ? 'salary-rules-tab' : 'salary-structures-tab'}
      >
        {tab === 'rules' ? (
          <RulesPanel rules={filteredRules} allRules={rules} loading={rulesQuery.isLoading} onOpen={openRule} />
        ) : (
          <StructuresPanel
            structures={filteredStructures}
            loading={structuresQuery.isLoading}
            onOpen={openStructure}
          />
        )}
      </div>

      <SalaryRuleForm
        open={ruleFormOpen}
        onOpenChange={setRuleFormOpen}
        rule={selectedRule}
        canManage={canManage}
      />
      <SalaryStructureForm
        open={structureFormOpen}
        onOpenChange={setStructureFormOpen}
        structure={selectedStructure}
        rules={rules}
        canManage={canManage}
      />
    </div>
  );
}

function RulesPanel({
  rules,
  allRules,
  loading,
  onOpen,
}: {
  rules: SalaryRule[];
  allRules: SalaryRule[];
  loading: boolean;
  onOpen: (rule: SalaryRule) => void;
}) {
  const categoryCounts = new Map(
    SALARY_CATEGORY_ORDER.map((category) => [
      category,
      allRules.filter((rule) => rule.category === category).length,
    ]),
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-x-5 gap-y-2" aria-label="Rule category coverage">
        {SALARY_CATEGORY_ORDER.map((category) => {
          const count = categoryCounts.get(category) ?? 0;
          return (
            <div key={category} className="flex items-center gap-1.5 text-xs text-slate-400">
              <CheckCircle2
                className={`h-3.5 w-3.5 ${count > 0 ? 'text-emerald-400' : 'text-slate-700'}`}
                aria-hidden="true"
              />
              {SALARY_CATEGORY_LABELS[category]}
              <span className="text-slate-400">{count}</span>
            </div>
          );
        })}
      </div>

      <Card className="overflow-hidden">
        <CardContent className="p-0">
          {loading ? (
            <LoadingRows />
          ) : rules.length === 0 ? (
            <EmptyState message="No salary rules match this view." />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Rule</TableHead>
                  <TableHead>Category</TableHead>
                  <TableHead>Computation</TableHead>
                  <TableHead className="text-right">Sequence</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rules.map((rule) => (
                  <TableRow key={rule.id}>
                    <TableCell>
                      <button
                        type="button"
                        onClick={() => onOpen(rule)}
                        className="group text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
                      >
                        <span className="block font-medium text-slate-200 group-hover:text-indigo-300">
                          {rule.name}
                        </span>
                        <span className="mt-0.5 block font-mono text-[11px] text-slate-400">{rule.code}</span>
                      </button>
                    </TableCell>
                    <TableCell>
                      <Badge variant={CATEGORY_BADGES[rule.category]} className="text-[10px]">
                        {SALARY_CATEGORY_LABELS[rule.category]}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <p className="text-xs text-slate-300">
                        {SALARY_COMPUTATION_LABELS[rule.computation_method]}
                      </p>
                      <p className="mt-0.5 max-w-xs truncate font-mono text-[11px] text-slate-400">
                        {describeComputation(rule)}
                      </p>
                    </TableCell>
                    <TableCell className="text-right font-mono text-xs text-slate-400">
                      {rule.sequence}
                    </TableCell>
                    <TableCell>
                      <Badge variant={rule.is_active ? 'success' : 'secondary'} className="text-[10px]">
                        {rule.is_active ? 'Active' : 'Inactive'}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function StructuresPanel({
  structures,
  loading,
  onOpen,
}: {
  structures: SalaryStructure[];
  loading: boolean;
  onOpen: (structure: SalaryStructure) => void;
}) {
  return (
    <Card className="overflow-hidden">
      <CardContent className="p-0">
        {loading ? (
          <LoadingRows />
        ) : structures.length === 0 ? (
          <EmptyState message="No salary structures match this view." />
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Structure</TableHead>
                <TableHead>Execution sequence</TableHead>
                <TableHead className="text-right">Rules</TableHead>
                <TableHead className="text-right">Contracts using</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {structures.map((structure) => (
                <TableRow key={structure.id}>
                  <TableCell>
                    <button
                      type="button"
                      onClick={() => onOpen(structure)}
                      className="group text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
                    >
                      <span className="block font-medium text-slate-200 group-hover:text-indigo-300">
                        {structure.name}
                      </span>
                      <span className="mt-0.5 block font-mono text-[11px] text-slate-400">
                        {structure.code}
                      </span>
                    </button>
                  </TableCell>
                  <TableCell>
                    {structure.rules.length === 0 ? (
                      <span className="text-xs text-slate-400">No rules</span>
                    ) : (
                      <div className="flex max-w-lg flex-wrap items-center gap-1.5">
                        {[...structure.rules]
                          .sort((a, b) => a.sequence - b.sequence)
                          .map((link, index) => (
                            <React.Fragment key={link.id}>
                              {index > 0 && (
                                <ArrowRight className="h-3 w-3 text-slate-400" aria-hidden="true" />
                              )}
                              <span className="font-mono text-[11px] text-slate-400">
                                {link.salary_rule.code.replace(/^PP360_/, '')}
                              </span>
                            </React.Fragment>
                          ))}
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="text-right font-medium text-slate-200">
                    {structure.rule_count}
                  </TableCell>
                  <TableCell className="text-right font-medium text-slate-200">
                    {structure.contract_usage_count}
                  </TableCell>
                  <TableCell>
                    <Badge variant={structure.is_active ? 'success' : 'secondary'} className="text-[10px]">
                      {structure.is_active ? 'Active' : 'Inactive'}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function describeComputation(rule: SalaryRule): string {
  if (rule.computation_method === SalaryRuleComputation.FORMULA) {
    return rule.expression ?? '—';
  }
  if (rule.computation_method === SalaryRuleComputation.PERCENTAGE) {
    return `${rule.amount ?? '0'}% of ${rule.percentage_base_code ?? '—'}`;
  }
  return rule.amount ?? '0.00';
}

function LoadingRows() {
  return (
    <div className="space-y-3 p-5" aria-label="Loading salary setup">
      {[0, 1, 2, 3].map((row) => (
        <div key={row} className="h-10 animate-pulse rounded-lg bg-slate-800/60" />
      ))}
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return <p className="px-4 py-14 text-center text-sm text-slate-400">{message}</p>;
}
