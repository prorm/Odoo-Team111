import * as React from 'react';
import { useNavigate } from 'react-router-dom';
import { Wallet } from 'lucide-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { FormField } from '@/components/FormField';
import { StatusMessage } from '@/components/StatusMessage';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
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
      navigate('/employees', { replace: true });
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
    <div className="flex min-h-screen items-center justify-center bg-slate-950 p-6">
      <div className="w-full max-w-sm space-y-4">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-tr from-indigo-600 to-indigo-400 shadow-lg shadow-indigo-950/50">
            <Wallet className="h-5 w-5 text-white" />
          </div>
          <div>
            <h1 className="text-lg font-bold tracking-tight text-slate-100">PeoplePay360</h1>
            <p className="text-[11px] text-slate-400">HR &amp; Payroll</p>
          </div>
        </div>

        <Card>
          <CardContent className="p-5">
            <form onSubmit={submit} className="space-y-4">
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
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
              Demo logins
            </p>
            <p className="mb-3 text-[11px] text-slate-400">
              Each role sees a different navigation and is refused different endpoints — the checks
              are server-side, so the difference is real rather than a hidden button.
            </p>
            <ul className="space-y-1">
              {DEMO_LOGINS.map((demo) => (
                <li key={demo.email}>
                  <button
                    type="button"
                    onClick={() => useDemoLogin(demo)}
                    disabled={login.isPending}
                    className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-xs text-slate-300 transition-colors hover:bg-slate-800 disabled:opacity-50"
                  >
                    <span className="truncate">{demo.role}</span>
                    <span className="ml-2 truncate font-mono text-[10px] text-slate-500">
                      {demo.email}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
