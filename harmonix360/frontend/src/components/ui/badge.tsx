import * as React from 'react';
import { cn } from '@/lib/utils';

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement> {
  variant?: 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning' | 'info';
}

function Badge({ className, variant = 'default', ...props }: BadgeProps) {
  const baseStyles =
    'inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2';

  const variants = {
    default: 'border-transparent bg-indigo-600/20 text-indigo-400 border border-indigo-500/30',
    secondary: 'border-transparent bg-slate-800 text-slate-300 border border-slate-700',
    destructive: 'border-transparent bg-red-950/60 text-red-400 border border-red-800/50',
    success: 'border-transparent bg-emerald-950/60 text-emerald-400 border border-emerald-800/50',
    warning: 'border-transparent bg-amber-950/60 text-amber-400 border border-amber-800/50',
    info: 'border-transparent bg-cyan-950/60 text-cyan-400 border border-cyan-800/50',
    outline: 'text-slate-300 border border-slate-700',
  };

  return <div className={cn(baseStyles, variants[variant], className)} {...props} />;
}

export { Badge };
