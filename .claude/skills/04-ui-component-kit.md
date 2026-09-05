# UI Component Kit

## Why this matters
Judges score production UI/UX explicitly: clean, responsive, structured spacing, semantic colors. With 4 people building UI independently, the app fragments into visibly different sub-apps unless everyone imports from one shared kit. This skill is the shared contract — build it in hour one, before anyone builds a page.

## Architecture
```
src/styles/tokens.css       — design tokens (colors, spacing, radius, shadow)
src/components/common/      — Button, Card, Table, Toast, LoadingSkeleton, EmptyState, ErrorState
src/components/layout/      — AppShell, Sidebar, Topbar
src/hooks/useAsync.js       — standardized loading/error/success state for any fetch
```
Every page composes from `common/` + `layout/` — nobody hand-rolls a button or a card shape.

## Step 1 — Design tokens
```css
/* src/styles/tokens.css */
:root {
  --color-primary: #2563eb;
  --color-primary-hover: #1d4ed8;
  --color-success: #16a34a;
  --color-success-bg: #dcfce7;
  --color-danger: #dc2626;
  --color-danger-bg: #fee2e2;
  --color-warning: #d97706;
  --color-warning-bg: #fef3c7;
  --color-bg: #f8fafc;
  --color-surface: #ffffff;
  --color-border: #e2e8f0;
  --color-text: #0f172a;
  --color-text-muted: #64748b;

  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 16px;

  --space-1: 4px;
  --space-2: 8px;
  --space-3: 16px;
  --space-4: 24px;
  --space-5: 32px;

  --shadow-card: 0 1px 3px rgba(0,0,0,0.08), 0 1px 2px rgba(0,0,0,0.04);
  --font-sans: 'Inter', system-ui, -apple-system, sans-serif;
}
```

## Step 2 — Core primitives
```jsx
// src/components/common/Button.jsx
export function Button({ variant = 'primary', loading, disabled, children, ...props }) {
  const base = 'inline-flex items-center justify-center gap-2 rounded-md px-4 py-2 text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed';
  const variants = {
    primary: 'bg-blue-600 text-white hover:bg-blue-700',
    secondary: 'bg-white border border-slate-200 text-slate-900 hover:bg-slate-50',
    danger: 'bg-red-600 text-white hover:bg-red-700',
  };
  return (
    <button className={`${base} ${variants[variant]}`} disabled={disabled || loading} {...props}>
      {loading && <Spinner size={14} />}
      {children}
    </button>
  );
}
```
```jsx
// src/components/common/Card.jsx
export function Card({ children, className = '' }) {
  return (
    <div className={`bg-white rounded-lg border border-slate-200 shadow-sm p-4 ${className}`}>
      {children}
    </div>
  );
}
```
```jsx
// src/components/common/Badge.jsx
const STATUS_STYLES = {
  PENDING:     'bg-amber-100 text-amber-800',
  CONFIRMED:   'bg-blue-100 text-blue-800',
  IN_PROGRESS: 'bg-indigo-100 text-indigo-800',
  COMPLETED:   'bg-green-100 text-green-800',
  CANCELLED:   'bg-slate-100 text-slate-600',
};

export function Badge({ status }) {
  return (
    <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_STYLES[status] ?? 'bg-slate-100 text-slate-600'}`}>
      {status.replace('_', ' ')}
    </span>
  );
}
```
Badge colors are keyed directly off the **State Machine** skill's status enum — one status set, one visual language, no drift.

## Step 3 — Standardized async states
```jsx
// src/hooks/useAsync.js
import { useState, useEffect, useCallback } from 'react';

export function useAsync(fetcher, deps = []) {
  const [state, setState] = useState({ status: 'loading', data: null, error: null });

  const run = useCallback(() => {
    setState({ status: 'loading', data: null, error: null });
    fetcher()
      .then(data => setState({ status: 'success', data, error: null }))
      .catch(error => setState({ status: 'error', data: null, error: error.message }));
  }, deps);

  useEffect(() => { run(); }, [run]);

  return { ...state, retry: run };
}
```
```jsx
// src/components/common/AsyncBoundary.jsx
export function AsyncBoundary({ status, error, retry, isEmpty, children }) {
  if (status === 'loading') return <LoadingSkeleton />;
  if (status === 'error') return <ErrorState message={error} onRetry={retry} />;
  if (isEmpty) return <EmptyState />;
  return children;
}

function LoadingSkeleton() {
  return (
    <div className="space-y-3 animate-pulse">
      {[...Array(3)].map((_, i) => <div key={i} className="h-16 bg-slate-100 rounded-md" />)}
    </div>
  );
}

function ErrorState({ message, onRetry }) {
  return (
    <Card className="text-center py-8">
      <p className="text-red-600 font-medium mb-2">Something went wrong</p>
      <p className="text-slate-500 text-sm mb-4">{message}</p>
      <Button variant="secondary" onClick={onRetry}>Retry</Button>
    </Card>
  );
}

function EmptyState({ message = 'Nothing here yet' }) {
  return <Card className="text-center py-12 text-slate-400">{message}</Card>;
}
```
```jsx
// usage in any list page
function ResourceList() {
  const { status, data, error, retry } = useAsync(() => api.get('/resources').then(r => r.data), []);
  return (
    <AsyncBoundary status={status} error={error} retry={retry} isEmpty={!data?.length}>
      <div className="grid grid-cols-3 gap-4">
        {data?.map(r => <ResourceCard key={r.id} resource={r} />)}
      </div>
    </AsyncBoundary>
  );
}
```

## Step 4 — Toast notifications (for websocket/action feedback)
```jsx
// src/components/common/Toast.jsx
import { createContext, useContext, useState, useCallback } from 'react';

const ToastContext = createContext(null);

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const push = useCallback((message, variant = 'success') => {
    const id = crypto.randomUUID();
    setToasts(t => [...t, { id, message, variant }]);
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 4000);
  }, []);

  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 space-y-2 z-50">
        {toasts.map(t => (
          <div key={t.id} className={`rounded-md px-4 py-2 shadow-lg text-sm text-white ${
            t.variant === 'error' ? 'bg-red-600' : 'bg-slate-900'
          }`}>{t.message}</div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
```

## Accessibility & responsive rules
- Every interactive element is a real `<button>`/`<a>`, never a `<div onClick>` — screen readers and keyboard nav depend on it.
- Color is never the only signal — `Badge` pairs color with text label, not color alone.
- Forms: every `<input>` has an associated `<label htmlFor>`, error text linked via `aria-describedby`.
- Breakpoint at minimum: `sm:` (mobile) stacks columns and collapses the sidebar to a hamburger; test at 375px width before demo day.
- Focus states preserved — don't `outline: none` without a visible replacement (`focus:ring-2 focus:ring-blue-500`).

## Checklist before moving on
- [ ] `tokens.css` imported once at the app root, no hardcoded hex/px anywhere else
- [ ] Every data-fetching component wrapped in `AsyncBoundary`
- [ ] All 3 role dashboards use the same `AppShell`/`Card`/`Button` primitives
- [ ] Tested at mobile width (375px) — sidebar collapses, no horizontal scroll
- [ ] Every status badge maps 1:1 to the state machine's enum values

## Common mistakes to avoid
- Free-styling a new color instead of adding it to `tokens.css` — breaks consistency and is hard to find/fix later.
- Building loading state as a lone spinner instead of a skeleton — skeletons read as more "production" to judges and reduce layout shift.
- Copy-pasting `Card`/`Button` code into a new component instead of importing — guarantees drift within days.

## Integration with the rest of the stack
- `Badge` status colors are driven by the **State Machine** skill's enum.
- `useToast` is what the **WebSocket Real-Time Sync** skill calls when a live event arrives, so the UI reacts to more than only your own actions.
