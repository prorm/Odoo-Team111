import * as React from 'react';
import { cn } from '@/lib/utils';

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning' | 'info';
}

function Badge({ className, variant = 'default', ...props }: BadgeProps) {
  const baseStyles =
    'inline-flex items-center rounded-sm border px-2 py-0.5 text-[11px] font-semibold leading-4 transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2';

  const variants = {
    default: 'border-indigo-800 bg-indigo-950 text-indigo-400',
    secondary: 'border-slate-800 bg-slate-950 text-slate-300',
    destructive: 'border-red-800 bg-red-950 text-red-400',
    success: 'border-emerald-800 bg-emerald-950 text-emerald-400',
    warning: 'border-amber-800 bg-amber-950 text-amber-400',
    info: 'border-cyan-800 bg-cyan-950 text-cyan-400',
    outline: 'border-slate-700 bg-white text-slate-300',
  };

  return <div className={cn(baseStyles, variants[variant], className)} {...props} />;
}

export { Badge };
