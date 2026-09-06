import { AlertTriangle, Info, XCircle } from 'lucide-react';

import { ApiError } from '@/lib/api-client';
import { cn } from '@/lib/utils';

interface StatusMessageProps {
  error: unknown;
  className?: string;
}

/**
 * Renders a failed request inline, and distinguishes the kind that the user can
 * do something about from the kind they cannot.
 *
 * A 409 on the Contract form is not an exception — it is the database
 * correctly refusing a second active contract over the same period, and HR
 * renewing a contract without ending the previous one will hit it routinely.
 * It gets a warning treatment and an explanation, because the message from the
 * server names the conflicting contract and its dates.
 *
 * A 500 gets an error treatment, because there is nothing in the form to fix.
 *
 * The distinction matters: a form that renders every failure identically
 * teaches people that red text means "something broke" rather than "here is
 * what to change".
 */
export function StatusMessage({ error, className }: StatusMessageProps) {
  if (!error) return null;

  const apiError = error instanceof ApiError ? error : null;
  const message = error instanceof Error ? error.message : String(error);

  const kind = apiError?.isConflict ? 'conflict' : apiError?.isForbidden ? 'forbidden' : 'error';

  const styles = {
    conflict: 'border-amber-500/40 bg-amber-500/10 text-amber-200',
    forbidden: 'border-slate-600/50 bg-slate-800/60 text-slate-300',
    error: 'border-rose-500/40 bg-rose-500/10 text-rose-200',
  }[kind];

  const Icon = kind === 'conflict' ? AlertTriangle : kind === 'forbidden' ? Info : XCircle;

  const heading = {
    conflict: 'This change conflicts with an existing record',
    forbidden: 'Your role does not permit this',
    error: 'Something went wrong',
  }[kind];

  return (
    <div
      role="alert"
      className={cn('flex gap-2.5 rounded-md border px-3 py-2.5 text-xs', styles, className)}
    >
      <Icon className="h-4 w-4 flex-shrink-0 mt-0.5" aria-hidden="true" />
      <div className="space-y-0.5">
        <p className="font-medium">{heading}</p>
        <p className="opacity-90 leading-relaxed">{message}</p>
      </div>
    </div>
  );
}
