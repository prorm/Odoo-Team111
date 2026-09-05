import { Button } from '@/components/ui/button';
import { usePayslipPreview, usePrintPayslip } from '@/hooks/usePayslipDocuments';
import { ErrorMessage } from '../hr-shared';

export function PayslipDocument({ id, printable }: { id: string; printable: boolean }) {
  const preview = usePayslipPreview(id);
  const print = usePrintPayslip(id);
  return <div className="space-y-3">
    <div className="flex items-center gap-3">
      <Button disabled={!printable || print.isPending} onClick={() => print.mutate()}>
        {print.isPending ? 'Preparing PDF…' : 'Print Payslip'}
      </Button>
      {!printable && <p className="text-xs text-slate-400">Validate this payrun to download payslips.</p>}
    </div>
    <ErrorMessage error={preview.error ?? print.error} />
    {preview.isLoading && <p className="text-sm text-slate-400">Loading payslip preview…</p>}
    {preview.data && <iframe title="Payslip preview" sandbox="" srcDoc={preview.data}
      className="h-[620px] w-full rounded-lg border border-slate-700 bg-white" />}
  </div>;
}
