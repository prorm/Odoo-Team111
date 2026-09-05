import { Badge } from '@/components/ui/badge';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { WARNING_LABELS, formatMoney } from '@/types/payroll';
import type { Payslip, PayslipLine } from '@/types/payroll';

/**
 * PS B7: the payslip, rule by rule.
 *
 * The table is the point of this screen. Every amount on a payslip is the
 * output of one Salary Rule at one position in the structure's sequence, and
 * showing the sequence and the category alongside the amount is what makes a
 * total explicable — "where did 12,000 come from" is answered by the row
 * above it, not by a support ticket.
 *
 * Amounts are rendered from the server's Decimal strings and never parsed
 * into a JavaScript number; see the note in types/payroll.ts.
 */

const CATEGORY_LABELS: Record<PayslipLine['category'], string> = {
  basic: 'Basic',
  allowance: 'Allowance',
  gross: 'Gross',
  deduction: 'Deduction',
  net: 'Net',
};

const CATEGORY_STYLES: Record<PayslipLine['category'], string> = {
  basic: 'text-slate-200',
  allowance: 'text-emerald-300',
  gross: 'text-slate-100 font-semibold',
  deduction: 'text-rose-300',
  net: 'text-indigo-200 font-semibold',
};

export function PayslipDetail({
  payslip,
  onClose,
}: {
  payslip: Payslip | undefined;
  onClose: () => void;
}) {
  if (!payslip) return null;

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-3xl">
        <DialogTitle>
          {payslip.employee.first_name} {payslip.employee.last_name}
        </DialogTitle>
        <DialogDescription>
          {payslip.payrun.name} · {payslip.payrun.period_start} to {payslip.payrun.period_end} ·
          computed against contract {payslip.contract.id} (wage {formatMoney(payslip.contract.wage)})
        </DialogDescription>

        <div className="mb-4 grid grid-cols-3 gap-3 text-sm">
          <Figure label="Worked days" value={payslip.worked_days} />
          <Figure label="Gross" value={formatMoney(payslip.gross_amount)} />
          <Figure label="Net" value={formatMoney(payslip.net_amount)} emphasis />
        </div>

        {payslip.warnings.length > 0 && (
          <ul className="mb-4 space-y-2">
            {payslip.warnings.map((warning, index) => (
              <li
                key={`${warning.code}-${index}`}
                className={`rounded-lg border p-3 text-sm ${
                  warning.severity === 'blocking'
                    ? 'border-rose-800/60 bg-rose-950/40 text-rose-200'
                    : 'border-amber-800/50 bg-amber-950/40 text-amber-200'
                }`}
              >
                <span className="font-medium">
                  {WARNING_LABELS[warning.code] ?? warning.code}
                </span>{' '}
                <span className="opacity-80">({warning.severity})</span>
                <p className="mt-1 opacity-90">{warning.message}</p>
              </li>
            ))}
          </ul>
        )}

        <div className="max-h-80 overflow-y-auto rounded-lg border border-slate-800">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-12">#</TableHead>
                <TableHead>Rule</TableHead>
                <TableHead>Code</TableHead>
                <TableHead>Category</TableHead>
                <TableHead className="text-right">Amount</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {payslip.lines.map((line) => (
                <TableRow key={line.id}>
                  <TableCell className="text-slate-500">{line.sequence}</TableCell>
                  <TableCell className="text-slate-200">{line.name}</TableCell>
                  <TableCell className="font-mono text-xs text-slate-400">{line.code}</TableCell>
                  <TableCell>
                    <Badge variant="secondary">{CATEGORY_LABELS[line.category]}</Badge>
                  </TableCell>
                  <TableCell className={`text-right tabular-nums ${CATEGORY_STYLES[line.category]}`}>
                    {formatMoney(line.amount)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>

        <p className="mt-3 text-xs text-slate-500">
          Every figure above was produced by the deterministic salary rule engine, in the sequence
          shown. Payslips are never edited: a wrong figure is corrected by fixing its input or its
          rule and recomputing the run.
        </p>
      </DialogContent>
    </Dialog>
  );
}

function Figure({ label, value, emphasis }: { label: string; value: string; emphasis?: boolean }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3">
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className={`mt-1 tabular-nums ${emphasis ? 'text-lg text-indigo-200' : 'text-slate-100'}`}>
        {value}
      </p>
    </div>
  );
}
