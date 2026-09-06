import * as React from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { FormField } from '@/components/FormField';
import { StatusMessage } from '@/components/StatusMessage';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { fetchApi } from '@/lib/api-client';
import { setToken } from '@/lib/auth';

interface TokenResponse {
  access_token: string;
  token_type: string;
}

/** The seeded demo logins, one per role (app/seed.py).
 *
 *  Listed in the UI on purpose: the point of having five roles is that someone
 *  can sign in as each and watch what changes, and making them hunt through a
 *  seed script for the passwords defeats that. These are demo credentials in
 *  version control, not a production credential path — a real deployment seeds
 *  no such users and this panel shows nothing. */
const DEMO_LOGINS = [
  { email: 'admin@peoplepay360.com', password: 'admin123', role: 'Admin' },
  { email: 'payroll.manager@peoplepay360.com', password: 'payroll123', role: 'HR Payroll Manager' },
  { email: 'payroll.user@peoplepay360.com', password: 'payroll123', role: 'HR Payroll User' },
  { email: 'hr.manager@peoplepay360.com', password: 'hrmanager123', role: 'HR Manager' },
  { email: 'employee@peoplepay360.com', password: 'employee123', role: 'Employee' },
];

export function LoginPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [email, setEmail] = React.useState('');
  const [password, setPassword] = React.useState('');

  // Set by lib/session.ts when it ends a session the server rejected.
  const expired = new URLSearchParams(window.location.search).has('expired');

  const login = useMutation({
    mutationFn: (credentials: { email: string; password: string }) =>
      fetchApi<TokenResponse>('/auth/login', {
        method: 'POST',
        body: JSON.stringify(credentials),
      }),
    onSuccess: (data) => {
      setToken(data.access_token);
      // Every cached query was fetched as the previous user (or as nobody).
      // Clearing rather than invalidating means none of it can be shown for a
      // moment under the new identity while a refetch is in flight.
      queryClient.clear();
      // Come back to whatever the expired session was looking at, when there
      // is one and it is a path within this app rather than an absolute URL
      // somebody appended to the query string.
      const from = new URLSearchParams(window.location.search).get('from');
      const safe = from && from.startsWith('/') && !from.startsWith('//') ? from : null;
      navigate(safe ?? '/employees', { replace: true });
    },
  });

  function submit(event: React.FormEvent) {
    event.preventDefault();
    login.mutate({ email, password });
  }

  function useDemoLogin(demo: (typeof DEMO_LOGINS)[number]) {
    setEmail(demo.email);
    setPassword(demo.password);
    login.mutate({ email: demo.email, password: demo.password });
  }

  return (
    <div className="min-h-screen bg-slate-950 lg:grid lg:grid-cols-[minmax(20rem,0.8fr)_minmax(32rem,1.2fr)]">
      <aside className="flex min-h-40 flex-col justify-between bg-[#13243A] p-6 text-white sm:p-8 lg:min-h-screen lg:p-12">
        <div>
          <p className="text-lg font-semibold tracking-tight">PeoplePay360</p>
          <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-[#AEBBCB]">
            HR &amp; Payroll
          </p>
        </div>

        <div className="mt-10 hidden max-w-sm lg:block">
          <p className="text-3xl font-semibold leading-tight tracking-tight text-white">
            People, payroll, and policy records in one working system.
          </p>
        </div>

        <p className="hidden text-xs text-[#AEBBCB] lg:block">PeoplePay360 · HR &amp; Payroll</p>
      </aside>

      <main className="flex items-start justify-center p-4 sm:p-8 lg:min-h-screen lg:items-center lg:p-12">
        <div className="w-full max-w-xl">
          <header className="mb-6 border-b border-slate-800 pb-5">
            <h1 className="text-2xl font-semibold tracking-tight text-slate-100">Sign in</h1>
            <p className="mt-1 text-sm text-slate-400">Use your PeoplePay360 account to continue.</p>
          </header>

          <section className="rounded-md border border-slate-800 bg-white p-5 sm:p-6">
            <form onSubmit={submit} className="space-y-4">
              {/* Says WHY they are here. Landing back on a blank sign-in form
                  mid-task otherwise reads as the app having lost their work. */}
              {expired && !login.error && (
                <p
                  role="status"
                  className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900"
                >
                  Your session timed out. Sign in again to pick up where you left off.
                </p>
              )}
              <StatusMessage error={login.error} />

              <FormField label="Email" htmlFor="login_email" required>
                <Input
                  id="login_email"
                  type="email"
                  autoComplete="username"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                />
              </FormField>

              <FormField label="Password" htmlFor="login_password" required>
                <Input
                  id="login_password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </FormField>

              <Button type="submit" className="w-full" disabled={login.isPending}>
                {login.isPending ? 'Signing in…' : 'Sign in'}
              </Button>
            </form>
          </section>

          <section className="mt-4 rounded-md border border-slate-800 bg-white">
            <div className="border-b border-slate-800 px-4 py-3">
              <p className="text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">
                Demo logins
              </p>
              <p className="mt-1 text-xs text-slate-400">
                Each role sees different navigation and server-enforced endpoint access.
              </p>
            </div>
            <ul className="divide-y divide-slate-800/70">
              {DEMO_LOGINS.map((demo) => (
                <li key={demo.email}>
                  <button
                    type="button"
                    onClick={() => useDemoLogin(demo)}
                    disabled={login.isPending}
                    className="flex min-h-10 w-full items-center justify-between gap-3 px-4 py-2 text-left text-xs text-slate-300 transition-colors hover:bg-slate-950 disabled:opacity-50"
                  >
                    <span className="font-medium">{demo.role}</span>
                    <span className="truncate text-[11px] text-slate-500">{demo.email}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </main>
    </div>
  );
}
