import * as React from 'react';
import { Check, ChevronDown, Search, X } from 'lucide-react';

import { Input } from '@/components/ui/input';
import { useManagerOptions } from '@/hooks/useHrApi';
import { cn } from '@/lib/utils';

interface ManagerSelectProps {
  value: string | null;
  /** The already-selected manager's name, so the field can show who is chosen
   *  before any search has run. Without it, editing an employee shows an empty
   *  box that looks like "no manager". */
  currentLabel: string | null;
  /** The employee being edited — never offered as their own manager. The
   *  server enforces this too; passing it here just keeps the option from
   *  appearing at all. */
  excludeId?: string;
  onChange: (id: string | null) => void;
}

/**
 * Searchable select over other employees (PS A1).
 *
 * A search field rather than a plain `<select>`: an organisation of any size
 * makes a full dropdown unusable, and the backend already exposes a `/lookup`
 * endpoint that returns only reference fields precisely so a picker does not
 * pull down every colleague's bank details.
 */
export function ManagerSelect({ value, currentLabel, excludeId, onChange }: ManagerSelectProps) {
  const [open, setOpen] = React.useState(false);
  const [search, setSearch] = React.useState('');
  const [debounced, setDebounced] = React.useState('');
  const containerRef = React.useRef<HTMLDivElement>(null);

  // Debounced so typing a name is one request, not one per keystroke.
  React.useEffect(() => {
    const timer = setTimeout(() => setDebounced(search), 250);
    return () => clearTimeout(timer);
  }, [search]);

  const { data: options, isFetching } = useManagerOptions(debounced, excludeId);

  const [selectedLabel, setSelectedLabel] = React.useState<string | null>(currentLabel);
  React.useEffect(() => setSelectedLabel(currentLabel), [currentLabel]);

  // Close on an outside click; a picker that stays open over the rest of the
  // form is worse than one that closes a moment too eagerly.
  React.useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [open]);

  function select(id: string, label: string) {
    onChange(id);
    setSelectedLabel(label);
    setOpen(false);
    setSearch('');
  }

  function clear(event: React.MouseEvent) {
    event.stopPropagation();
    onChange(null);
    setSelectedLabel(null);
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((previous) => !previous)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex h-10 w-full items-center justify-between rounded-lg border border-slate-800 bg-slate-950/80 px-3 text-left text-sm text-slate-100 transition-colors hover:border-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
      >
        <span className={cn(!value && 'text-slate-500')}>
          {value ? selectedLabel ?? 'Selected' : 'No manager'}
        </span>
        <span className="flex items-center gap-1">
          {value && (
            <span
              role="button"
              tabIndex={0}
              aria-label="Clear manager"
              onClick={clear}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') clear(e as unknown as React.MouseEvent);
              }}
              className="rounded p-0.5 text-slate-500 hover:text-slate-200"
            >
              <X className="h-3.5 w-3.5" />
            </span>
          )}
          <ChevronDown className="h-4 w-4 text-slate-500" aria-hidden="true" />
        </span>
      </button>

      {open && (
        <div className="absolute z-50 mt-1 w-full rounded-lg border border-slate-800 bg-slate-900 shadow-xl">
          <div className="relative border-b border-slate-800 p-2">
            <Search className="absolute left-4 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-500" />
            <Input
              autoFocus
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search employees…"
              className="h-8 pl-7 text-xs"
              aria-label="Search for a manager"
            />
          </div>

          <ul role="listbox" className="max-h-56 overflow-y-auto py-1">
            {isFetching && options === undefined && (
              <li className="px-3 py-2 text-xs text-slate-500">Searching…</li>
            )}
            {options?.length === 0 && (
              <li className="px-3 py-2 text-xs text-slate-500">No matching employees.</li>
            )}
            {options?.map((option) => {
              const label = `${option.first_name} ${option.last_name}`;
              const isSelected = option.id === value;
              return (
                <li key={option.id}>
                  <button
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    onClick={() => select(option.id, label)}
                    className="flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-xs text-slate-200 hover:bg-slate-800"
                  >
                    <span className="min-w-0">
                      <span className="block truncate">{label}</span>
                      <span className="block truncate text-[11px] text-slate-500">
                        {option.job_position ?? option.work_email}
                      </span>
                    </span>
                    {isSelected && <Check className="h-3.5 w-3.5 flex-shrink-0 text-indigo-400" />}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
