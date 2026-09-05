import * as React from 'react';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Bell, Building2, ChevronRight, LogOut, Menu, Search, Wallet, X } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';

import { ConflictModal } from '@/components/ConflictModal';
import { OfflineBanner } from '@/components/OfflineBanner';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ToastProvider } from '@/components/ui/toast';
import { useCurrentUser } from '@/hooks/useCurrentUser';
import { clearToken } from '@/lib/auth';
import { visibleNavItems } from '@/lib/navigation';
import { ROLE_LABELS } from '@/types/enums';

function initials(name: string | undefined): string {
  if (!name) return '··';
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');
}

export function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [mobileOpen, setMobileOpen] = React.useState(false);
  const { data: user } = useCurrentUser();

  // Until /auth/me answers, render no nav rather than the full nav. Showing
  // everything and then removing entries would flash Payroll at an HR Manager
  // who may not open it — a confusing first impression of what they can do.
  const navItems = visibleNavItems(user?.role);
  const currentSection = navItems.find((item) => location.pathname.startsWith(item.href));

  // Close the mobile drawer on navigation; leaving it open covers the page the
  // user just asked for.
  React.useEffect(() => {
    setMobileOpen(false);
  }, [location.pathname]);

  return (
    <ToastProvider>
      <div className="min-h-screen bg-slate-950 text-slate-100 flex overflow-hidden">
        {mobileOpen && (
          <div
            className="fixed inset-0 bg-slate-950/80 z-40 lg:hidden backdrop-blur-sm"
            onClick={() => setMobileOpen(false)}
          />
        )}

        <aside
          className={`fixed lg:static inset-y-0 left-0 z-50 w-64 bg-slate-900/95 border-r border-slate-800/80 flex flex-col transition-transform duration-300 ease-in-out ${
            mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'
          }`}
        >
          <div className="h-16 px-6 flex items-center justify-between border-b border-slate-800/80 bg-slate-950/40">
            <div className="flex items-center space-x-3">
              <div className="h-9 w-9 rounded-xl bg-gradient-to-tr from-indigo-600 to-indigo-400 flex items-center justify-center shadow-lg shadow-indigo-950/50">
                <Wallet className="h-5 w-5 text-white" />
              </div>
              <div>
                <span className="font-bold text-lg text-slate-100 tracking-tight">PeoplePay360</span>
                <p className="text-[11px] text-slate-400 flex items-center gap-1">
                  <Building2 className="h-3 w-3 text-slate-500" /> HR &amp; Payroll
                </p>
              </div>
            </div>
            <button
              onClick={() => setMobileOpen(false)}
              className="lg:hidden text-slate-400 hover:text-white"
              aria-label="Close navigation"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          <nav className="flex-1 py-6 px-3 space-y-1 overflow-y-auto" aria-label="Main">
            {navItems.map((item) => {
              const active = location.pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  to={item.href}
                  aria-current={active ? 'page' : undefined}
                  className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors ${
                    active
                      ? 'bg-indigo-600/15 text-indigo-300 border border-indigo-500/30'
                      : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-100'
                  }`}
                >
                  <item.icon className="h-4 w-4" />
                  {item.name}
                </Link>
              );
            })}
          </nav>

          <div className="p-4 border-t border-slate-800/80 bg-slate-950/40">
            <div className="flex items-center justify-between gap-2 overflow-hidden">
              <div className="flex items-center space-x-3 overflow-hidden">
                <div className="h-8 w-8 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center text-slate-300 font-semibold text-xs flex-shrink-0">
                  {initials(user?.name)}
                </div>
                <div className="truncate">
                  <div className="text-xs font-medium text-slate-200 truncate">
                    {user?.email ?? 'Not signed in'}
                  </div>
                  {user?.role && (
                    <div className="flex items-center gap-1 mt-0.5">
                      <Badge variant="default" className="text-[9px] px-1.5 py-0">
                        {ROLE_LABELS[user.role] ?? user.role}
                      </Badge>
                    </div>
                  )}
                </div>
              </div>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Sign out"
                title="Sign out"
                onClick={() => {
                  clearToken();
                  // Clear, not invalidate: every cached row was fetched under
                  // the previous role, and none of it should be visible for
                  // even a frame under the next one.
                  queryClient.clear();
                  navigate('/login', { replace: true });
                }}
                className="flex-shrink-0 text-slate-400 hover:text-slate-100"
              >
                <LogOut className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </aside>

        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
          <OfflineBanner />
          <ConflictModal />

          <header className="h-16 px-6 bg-slate-900/80 border-b border-slate-800/80 flex items-center justify-between backdrop-blur-md sticky top-0 z-30">
            <div className="flex items-center space-x-4">
              <button
                onClick={() => setMobileOpen(true)}
                className="lg:hidden text-slate-400 hover:text-white"
                aria-label="Open navigation"
              >
                <Menu className="h-6 w-6" />
              </button>

              <div className="flex items-center text-sm text-slate-400 space-x-2">
                <span>PeoplePay360</span>
                <ChevronRight className="h-4 w-4 text-slate-600" />
                <span className="font-semibold text-slate-100">{currentSection?.name ?? 'Home'}</span>
              </div>
            </div>

            <div className="flex items-center space-x-3">
              <div className="relative hidden md:block">
                <Search className="h-4 w-4 absolute left-3 top-2.5 text-slate-500" />
                <input
                  type="search"
                  placeholder="Search..."
                  aria-label="Search"
                  className="h-9 w-64 rounded-lg bg-slate-950/80 border border-slate-800 pl-9 pr-4 text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
              </div>

              <Button variant="ghost" size="icon" className="relative text-slate-400 hover:text-white" aria-label="Notifications">
                <Bell className="h-4 w-4" />
              </Button>
            </div>
          </header>

          <main className="flex-1 overflow-y-auto p-6 lg:p-8 bg-slate-950">
            <Outlet />
          </main>
        </div>
      </div>
    </ToastProvider>
  );
}
