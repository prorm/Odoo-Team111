import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog';
import { ViewCalculation } from '@/components/insights/ViewCalculation';
import { WARNING_LABELS } from '@/types/payroll';
import type { Payslip } from '@/types/payroll';
import { PayslipDocument } from './PayslipDocument';

/** Monetary preview and PDF share the server template; warnings belong to the app. */
export function PayslipDetail({ payslip, onClose }: {
  payslip: Payslip | undefined; onClose: () => void;
}) {
  if (!payslip) return null;
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-4xl max-h-[95vh] overflow-y-auto">
        <DialogTitle>{payslip.employee.first_name} {payslip.employee.last_name}</DialogTitle>
        <DialogDescription>
          {payslip.payrun.name}
          {payslip.payrun.salary_structure
            ? ` · structure ${payslip.payrun.salary_structure.code}`
            : ''}{' '}
          · {payslip.payrun.period_start} to {payslip.payrun.period_end} · {payslip.status}
        </DialogDescription>
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

        <PayslipDocument id={payslip.id} printable={payslip.status === 'validated' || payslip.status === 'paid'} />

        {/* PRD §5.6/§5.9. Read-only, below the document: the payslip is the
            record, this explains how it was produced. */}
        <div className="mt-6 border-t border-slate-800 pt-4">
          <ViewCalculation payslipId={payslip.id} />
        </div>
      </DialogContent>
    </Dialog>
  );
}
