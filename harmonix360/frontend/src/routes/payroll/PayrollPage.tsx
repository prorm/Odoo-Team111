import { useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Select } from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { usePayruns } from '@/hooks/usePayroll';

import { ErrorMessage, Pagination } from '../hr-shared';
import { PayrunWizard } from './PayrunWizard';
import { STATUS_VARIANT } from './status';

/**
 * PS B5/B6's payroll surface: the list of runs, and the wizard that creates
 * one.
 *
 * Finalized runs are listed alongside open ones rather than archived away —
 * "finalized runs preserved as history" (PS B6) means somebody has to be able
 * to find last March, and a history nobody can reach is not preserved.
 */
export function PayrollPage() {
  const navigate = useNavigate();
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState('');
  const [wizardOpen, setWizardOpen] = useState(false);
  const payruns = usePayruns(offset, status || undefined);

  return (
    <div className="space-y-5">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Payroll</h1>
          <p className="mt-1 text-xs text-slate-400">
            Payruns execute one salary structure over one period, for an explicitly selected set of
            employees.
          </p>
        </div>
        <Button onClick={() => setWizardOpen(true)}>New payrun</Button>
      </header>

      <div className="flex flex-wrap items-center gap-3">
        <Select
          aria-label="Filter by status"
          className="w-48"
          value={status}
          onChange={(event) => {
            setStatus(event.target.value);
            setOffset(0);
          }}
        >
          <option value="">All statuses</option>
          <option value="draft">Draft</option>
          <option value="computed">Computed</option>
          <option value="validated">Validated</option>
          <option value="paid">Paid</option>
        </Select>
      </div>

      <ErrorMessage error={payruns.error} />

      <div className="rounded-lg border border-slate-800">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Payrun</TableHead>
              <TableHead>Period</TableHead>
              <TableHead>Structure</TableHead>
              <TableHead className="text-right">Employees</TableHead>
              <TableHead className="text-right">Payslips</TableHead>
              <TableHead>Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {(payruns.data?.items ?? []).map((payrun) => (
              <TableRow
                key={payrun.id}
                className="cursor-pointer hover:bg-slate-800/40"
                onClick={() => navigate(`/payroll/${payrun.id}`)}
              >
                <TableCell className="text-slate-200">{payrun.name}</TableCell>
                <TableCell className="text-slate-400">
                  {payrun.period_start} → {payrun.period_end}
                </TableCell>
                <TableCell className="text-slate-400">{payrun.salary_structure.code}</TableCell>
                <TableCell className="text-right tabular-nums text-slate-300">
                  {payrun.employees.length}
                </TableCell>
                <TableCell className="text-right tabular-nums text-slate-300">
                  {payrun.payslip_count}
                </TableCell>
                <TableCell>
                  <Badge variant={STATUS_VARIANT[payrun.status]}>{payrun.status}</Badge>
                </TableCell>
              </TableRow>
            ))}
            {!payruns.isLoading && (payruns.data?.items.length ?? 0) === 0 && (
              <TableRow>
                <TableCell colSpan={6} className="p-6 text-center text-sm text-slate-400">
                  No payruns yet. Create one to compute payslips for a period.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <Pagination offset={offset} total={payruns.data?.total ?? 0} onChange={setOffset} />

      <PayrunWizard
        open={wizardOpen}
        onOpenChange={setWizardOpen}
        onCreated={(payrun) => navigate(`/payroll/${payrun.id}`)}
      />
    </div>
  );
}
