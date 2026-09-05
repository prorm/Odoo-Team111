import { useState } from 'react';
import { useDeliveryStatuses } from '@/hooks/usePayslipDocuments';
import { Link, useParams } from 'react-router-dom';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  useComputePayrun,
  usePayrun,
  usePayrunPayslips,
  usePayrunTransition,
  usePayrunValidation,
  useSendPayslips,
} from '@/hooks/usePayroll';
import { formatMoney } from '@/types/payroll';
import type { ComputeResult, Payslip } from '@/types/payroll';

import { FirewallPanel } from '@/components/insights/FirewallPanel';

import { ErrorMessage } from '../hr-shared';
import { PayslipDetail } from './PayslipDetail';
import { STATUS_VARIANT } from './status';

/**
 * PS B6: Compute / Validate / Mark Paid / Send Payslips, with warnings
 * surfaced before finalization.
 *
 * The screen is built around the ratchet the server enforces — DRAFT →
 * COMPUTED → VALIDATED → PAID — so exactly one action is ever the next one.
 * Disabling the others is a courtesy; the server refuses them regardless, and
 * a 409 here is rendered inline rather than swallowed, because every one of
 * them means something the user can act on ("someone else moved this run",
 * "fix these records first").
 *
 * The blocking-issue panel is PRD §5.10's validation firewall. It stays
 * visible while any blocking issue stands, and Validate stays disabled — the
 * gate is not something the UI decides, it is the server's answer rendered.
 */
export function PayrunDetailPage() {
  const { payrunId } = useParams();
  const payrun = usePayrun(payrunId);
  const deliveries = useDeliveryStatuses(payrunId, payrun.data?.status === 'paid');
  const payslips = usePayrunPayslips(payrunId);
  const validation = usePayrunValidation(payrunId, payrun.data?.status !== 'draft');

  const compute = useComputePayrun();
  const validate = usePayrunTransition('validate');
  const markPaid = usePayrunTransition('mark-paid');
  const send = useSendPayslips();

  const [lastCompute, setLastCompute] = useState<ComputeResult>();
  const [open, setOpen] = useState<Payslip>();

  if (payrun.isLoading) return <p className="p-6 text-sm text-slate-400">Loading payrun…</p>;
  if (payrun.error) return <div className="p-6"><ErrorMessage error={payrun.error} /></div>;
  if (!payrun.data) return null;

  const run = payrun.data;
  const blocking = validation.data?.blocking_count ?? 0;
  const finalized = run.status === 'validated' || run.status === 'paid';

  // Exactly one action is ever the next one, so exactly one button carries the
  // primary emphasis. Without this, a paid run still shows Compute as the
  // loudest control on the screen — an action that cannot be taken, styled as
  // the one you should take.
  const next: 'compute' | 'validate' | 'mark-paid' | 'send' | null =
    run.status === 'draft'
      ? 'compute'
      : run.status === 'computed'
        ? blocking > 0
          ? 'compute'
          : 'validate'
        : run.status === 'validated'
          ? 'mark-paid'
          : run.status === 'paid'
            ? 'send'
            : null;
  const emphasis = (action: typeof next) => (next === action ? 'default' : 'outline');

  return (
    <div className="space-y-6 p-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to="/payroll" className="text-sm text-indigo-400 hover:underline">
            ← All payruns
          </Link>
          <h1 className="mt-2 text-2xl font-semibold text-slate-100">{run.name}</h1>
          <p className="mt-1 text-sm text-slate-400">
            {run.period_start} to {run.period_end} · structure {run.salary_structure.name} (
            {run.salary_structure.code}) · {run.employees.length} employee
            {run.employees.length === 1 ? '' : 's'} selected · {run.payslip_count} payslip
            {run.payslip_count === 1 ? '' : 's'}
          </p>
        </div>
        <Badge variant={STATUS_VARIANT[run.status]}>{run.status}</Badge>
      </header>

      <section className="flex flex-wrap gap-3">
        <Button
          variant={emphasis('compute')}
          disabled={finalized || compute.isPending}
          onClick={async () => {
            const result = await compute.mutateAsync({ payrunId: run.id, version: run.version });
            setLastCompute(result);
          }}
        >
          {compute.isPending ? 'Computing…' : run.status === 'draft' ? 'Compute' : 'Recompute'}
        </Button>
        <Button
          variant={emphasis('validate')}
          disabled={run.status !== 'computed' || blocking > 0 || validate.isPending}
          onClick={() => validate.mutateAsync({ payrunId: run.id, version: run.version })}
          title={
            blocking > 0
              ? 'Resolve the blocking issues below, then recompute'
              : 'Validate this payrun'
          }
        >
          {validate.isPending ? 'Validating…' : 'Validate'}
        </Button>
        <Button
          variant={emphasis('mark-paid')}
          disabled={run.status !== 'validated' || markPaid.isPending}
          onClick={() => markPaid.mutateAsync({ payrunId: run.id, version: run.version })}
        >
          {markPaid.isPending ? 'Marking…' : 'Mark paid'}
        </Button>
        <Button
          variant={emphasis('send')}
          disabled={run.status !== 'paid' || send.isPending}
          onClick={() => send.mutateAsync({ payrunId: run.id })}
        >
          {send.isPending ? 'Queueing…' : 'Send payslips'}
        </Button>
      </section>

      <ErrorMessage
        error={compute.error ?? validate.error ?? markPaid.error ?? send.error}
      />
      <ErrorMessage error={deliveries.error} />
      {run.status === 'paid' && <p className="text-xs text-slate-400">
        Email status updates automatically. Sent means accepted by the mail server.
        Send payslips retries pending or failed deliveries and skips those already sent.
      </p>}

      {send.data && (
        <p className="rounded-lg border border-emerald-800/50 bg-emerald-950/40 p-3 text-sm text-emerald-200">
          {send.data.detail}
        </p>
      )}

      {finalized && (
        <p className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-sm text-slate-300">
          This run is {run.status} and is preserved as history. Its payslips can no longer be
          recomputed, edited or deleted — correcting a finalized run means creating a new one.
        </p>
      )}

      {lastCompute && lastCompute.skipped.length > 0 && (
        <section className="rounded-lg border border-rose-800/60 bg-rose-950/30 p-4">
          <h2 className="text-sm font-semibold text-rose-200">
            {lastCompute.skipped.length} selected employee
            {lastCompute.skipped.length === 1 ? '' : 's'} could not be computed
          </h2>
          <ul className="mt-2 space-y-1 text-sm text-rose-200/90">
            {lastCompute.skipped.map((row) => (
              <li key={row.employee_id}>
                <span className="font-medium">{row.employee_name}</span> — {row.reason}
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* PRD §5.10, Phase 10: the same findings, grouped by code with the fix
          and a link to the record that has to change. `usePayrunValidation`
          still backs the Validate button's disabled state above; this panel is
          the visibility layer over the identical server report. */}
      {run.status !== 'draft' && (
        <FirewallPanel
          payrunId={run.id}
          onRevalidate={() => validate.mutate({ payrunId: run.id, version: run.version })}
          revalidating={validate.isPending}
          revalidateError={validate.error}
        />
      )}

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">
          Payslips
        </h2>
        <div className="rounded-lg border border-slate-800">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Employee</TableHead>
                <TableHead className="text-right">Worked days</TableHead>
                <TableHead className="text-right">Gross</TableHead>
                <TableHead className="text-right">Net</TableHead>
                <TableHead>Warnings</TableHead>
                <TableHead>Email</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {(payslips.data?.items ?? []).map((payslip) => {
                const delivery = deliveries.data?.items.find((item) => item.payslip_id === payslip.id);
                const blockingCount = payslip.warnings.filter(
                  (warning) => warning.severity === 'blocking'
                ).length;
                return (
                  <TableRow key={payslip.id}>
                    <TableCell className="text-slate-200">
                      {payslip.employee.first_name} {payslip.employee.last_name}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-slate-300">
                      {payslip.worked_days}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-slate-300">
                      {formatMoney(payslip.gross_amount)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-slate-100">
                      {formatMoney(payslip.net_amount)}
                    </TableCell>
                    <TableCell>
                      {payslip.warnings.length === 0 ? (
                        <span className="text-xs text-slate-500">None</span>
                      ) : (
                        <Badge variant={blockingCount > 0 ? 'destructive' : 'warning'}>
                          {blockingCount > 0
                            ? `${blockingCount} blocking`
                            : `${payslip.warnings.length} advisory`}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      {delivery?.queued ? <div className="text-left text-xs">
                        <span className={delivery.status === 'failed' ? 'text-rose-300' : delivery.status === 'sent' ? 'text-emerald-300' : 'text-slate-300'}>{delivery.status}</span>
                        {delivery.error && <p className="max-w-xs text-rose-300">{delivery.error}</p>}
                      </div> : <span className="text-xs text-slate-500">Not queued</span>}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm" onClick={() => setOpen(payslip)}>
                        View calculation
                      </Button>
                    </TableCell>
                  </TableRow>
                );
              })}
              {(payslips.data?.items.length ?? 0) === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className="p-6 text-center text-sm text-slate-400">
                    No payslips yet — compute this payrun to produce them.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </section>

      <PayslipDetail payslip={open} onClose={() => setOpen(undefined)} />
    </div>
  );
}
