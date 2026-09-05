import * as React from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { CheckCircle2, Pencil, Plus, Trash2 } from 'lucide-react';

import { StatusMessage } from '@/components/StatusMessage';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Select } from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useContracts, useDeleteContract, useEmployees } from '@/hooks/useHrApi';
import type { Contract } from '@/types/hr';
import { CONTRACT_STATUS_LABELS, ContractStatus } from '@/types/hr';

import { ContractForm } from './ContractForm';

const STATUS_VARIANT: Record<ContractStatus, 'success' | 'secondary' | 'outline' | 'destructive'> = {
  [ContractStatus.DRAFT]: 'outline',
  [ContractStatus.ACTIVE]: 'success',
  [ContractStatus.EXPIRED]: 'secondary',
  [ContractStatus.CANCELLED]: 'destructive',
};

/**
 * Contract management (PS A2).
 *
 * The `?employee=` parameter is what the Employee form's contract smart button
 * navigates to, so arriving from there opens the list already filtered to that
 * person's history rather than showing everyone.
 */
export function ContractsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const employeeFilter = searchParams.get('employee') ?? '';
  const statusFilter = searchParams.get('status') ?? '';

  const [editing, setEditing] = React.useState<Contract | undefined>();
  const [formOpen, setFormOpen] = React.useState(false);

  const { data, isLoading, error } = useContracts({
    employee_id: employeeFilter || undefined,
    status: statusFilter || undefined,
    limit: 200,
  });
  const { data: employees } = useEmployees({ limit: 200 });
  const remove = useDeleteContract();

  const contracts = data?.items ?? [];

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(key, value);
    else next.delete(key);
    // Filters live in the URL so a filtered view is linkable and survives a
    // reload — which is what makes the smart button's deep link work at all.
    setSearchParams(next, { replace: true });
  }

  function openCreate() {
    setEditing(undefined);
    setFormOpen(true);
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Contracts</h1>
          <p className="mt-0.5 text-xs text-slate-400">
            An employee may hold many contracts over time, but only one can be active for any given
            period.
          </p>
        </div>
        <Button size="sm" onClick={openCreate}>
          <Plus className="mr-1.5 h-3.5 w-3.5" />
          New contract
        </Button>
      </header>

      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 p-4">
          <Select
            value={employeeFilter}
            onChange={(e) => setFilter('employee', e.target.value)}
            aria-label="Filter by employee"
            className="w-auto min-w-[200px]"
          >
            <option value="">All employees</option>
            {employees?.items.map((employee) => (
              <option key={employee.id} value={employee.id}>
                {employee.full_name}
              </option>
            ))}
          </Select>

          <Select
            value={statusFilter}
            onChange={(e) => setFilter('status', e.target.value)}
            aria-label="Filter by status"
            className="w-auto min-w-[150px]"
          >
            <option value="">All statuses</option>
            {Object.values(ContractStatus).map((value) => (
              <option key={value} value={value}>
                {CONTRACT_STATUS_LABELS[value]}
              </option>
            ))}
          </Select>
        </CardContent>
      </Card>

      <StatusMessage error={error ?? remove.error} />

      {isLoading && <p className="text-sm text-slate-400">Loading contracts…</p>}

      {!isLoading && contracts.length === 0 && (
        <Card>
          <CardContent className="py-12 text-center">
            <p className="text-sm text-slate-300">No contracts match these filters.</p>
          </CardContent>
        </Card>
      )}

      {!isLoading && contracts.length > 0 && (
        <Card>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Employee</TableHead>
                  <TableHead>Position</TableHead>
                  <TableHead>Period</TableHead>
                  <TableHead className="text-right">Wage</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="w-20" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {contracts.map((contract) => (
                  <TableRow
                    key={contract.id}
                    // The currently-active contract is highlighted (PS A2).
                    // `is_currently_active` is computed server-side against the
                    // employee's whole contract set — a page may not hold all
                    // of it, so this cannot be derived here.
                    className={contract.is_currently_active ? 'bg-emerald-500/[0.07]' : undefined}
                  >
                    <TableCell>
                      <Link
                        to={`/employees/${contract.employee.id}`}
                        className="font-medium text-slate-100 hover:text-indigo-300"
                      >
                        {contract.employee.first_name} {contract.employee.last_name}
                      </Link>
                    </TableCell>
                    <TableCell className="text-slate-300">{contract.job_position ?? '—'}</TableCell>
                    <TableCell className="whitespace-nowrap text-slate-300">
                      {contract.start_date} → {contract.end_date ?? 'open-ended'}
                    </TableCell>
                    <TableCell className="text-right font-mono text-slate-200">
                      {/* Rendered as the string the server sent. Parsing it into
                          a JS number would make it a float, and a float cannot
                          represent 0.10 exactly. */}
                      {contract.wage}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        <Badge variant={STATUS_VARIANT[contract.status]} className="text-[9px]">
                          {CONTRACT_STATUS_LABELS[contract.status]}
                        </Badge>
                        {contract.is_currently_active && (
                          <span
                            className="flex items-center gap-1 text-[10px] text-emerald-300"
                            title="This is the contract payroll resolves for today"
                          >
                            <CheckCircle2 className="h-3 w-3" />
                            Current
                          </span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex justify-end gap-0.5">
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label="Edit contract"
                          onClick={() => {
                            setEditing(contract);
                            setFormOpen(true);
                          }}
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label="Delete contract"
                          onClick={() => remove.mutate(contract.id)}
                        >
                          <Trash2 className="h-3.5 w-3.5 text-rose-400" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </Card>
      )}

      <ContractForm
        open={formOpen}
        onOpenChange={setFormOpen}
        contract={editing}
        defaultEmployeeId={employeeFilter || undefined}
      />
    </div>
  );
}
