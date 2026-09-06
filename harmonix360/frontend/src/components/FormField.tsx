import * as React from 'react';

import { cn } from '@/lib/utils';

interface FormFieldProps {
  label: string;
  htmlFor?: string;
  required?: boolean;
  /** A field-specific error, rendered under the control.
   *
   *  Errors belong next to the control that caused them, not only in a summary
   *  at the top — on a form as long as the Employee form, a top-of-form message
   *  can be scrolled out of sight while the user stares at the field that is
   *  actually wrong. */
  error?: string | null;
  hint?: string;
  className?: string;
  children: React.ReactNode;
}

export function FormField({
  label,
  htmlFor,
  required,
  error,
  hint,
  className,
  children,
}: FormFieldProps) {
  const errorId = error && htmlFor ? `${htmlFor}-error` : undefined;
  const hintId = hint && htmlFor ? `${htmlFor}-hint` : undefined;

  return (
    <div className={cn('space-y-1.5', className)}>
      <label htmlFor={htmlFor} className="block text-xs font-medium text-slate-300">
        {label}
        {required && (
          <span className="text-rose-400 ml-0.5" aria-hidden="true">
            *
          </span>
        )}
      </label>
      {/* aria-describedby is wired by the caller via these ids; screen readers
          announce the error with the field rather than as loose text. */}
      <div aria-describedby={cn(errorId, hintId) || undefined}>{children}</div>
      {hint && !error && (
        <p id={hintId} className="text-[11px] text-slate-400">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} role="alert" className="text-[11px] text-rose-400">
          {error}
        </p>
      )}
    </div>
  );
}
