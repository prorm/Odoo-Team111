import * as React from 'react';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Bell, ChevronRight, LogOut, Menu, Search, X } from 'lucide-react';
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
      <div className="flex min-h-screen overflow-hidden bg-slate-950 text-slate-100">
        {mobileOpen && (
          <div
            className="fixed inset-0 z-40 bg-[#13243A]/55 lg:hidden"
            onClick={() => setMobileOpen(false)}
          />
        )}

        <aside
          className={`fixed inset-y-0 left-0 z-50 flex w-56 flex-col border-r border-[#0D1C2E] bg-[#13243A] text-white transition-transform duration-200 ease-out lg:static ${
            mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'
          }`}
        >
          <div className="flex h-16 items-center justify-between border-b border-white/10 px-4">
            <div className="min-w-0">
              <span className="block truncate text-[15px] font-semibold tracking-tight text-white">PeoplePay360</span>
              <p className="text-[10px] font-medium uppercase tracking-[0.08em] text-[#AEBBCB]">
                HR &amp; Payroll
              </p>
            </div>
            <button
              onClick={() => setMobileOpen(false)}
              className="flex h-10 w-10 items-center justify-center rounded-md text-[#AEBBCB] hover:bg-white/10 hover:text-white lg:hidden"
              aria-label="Close navigation"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          <nav className="flex-1 space-y-1 overflow-y-auto px-3 py-4" aria-label="Main">
            {navItems.map((item) => {
              const active = location.pathname.startsWith(item.href);
              return (
                <Link
                  key={item.href}
                  to={item.href}
                  aria-current={active ? 'page' : undefined}
                  className={`flex min-h-9 items-center gap-2.5 rounded-md px-3 py-2 text-[13px] font-medium transition-colors ${
                    active
                      ? 'bg-[#0B6670] text-white'
                      : 'text-[#C4CEDA] hover:bg-white/10 hover:text-white'
                  }`}
                >
                  <item.icon className="h-4 w-4" />
                  {item.name}
                </Link>
              );
            })}
          </nav>

          <div className="border-t border-white/10 p-3">
            <div className="flex items-center justify-between gap-2 overflow-hidden">
              <div className="flex items-center space-x-3 overflow-hidden">
                <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-md border border-white/15 bg-white/10 text-xs font-semibold text-white">
                  {initials(user?.name)}
                </div>
                <div className="truncate">
                  <div className="truncate text-xs font-medium text-white">
                    {user?.email ?? 'Not signed in'}
                  </div>
                  {user?.role && (
                    <div className="flex items-center gap-1 mt-0.5">
                      <Badge variant="default" className="border-white/15 bg-white/10 px-1.5 py-0 text-[9px] text-[#DCE4EC]">
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
                className="flex-shrink-0 text-[#AEBBCB] hover:bg-white/10 hover:text-white"
              >
                <LogOut className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </aside>

        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
          <OfflineBanner />
          <ConflictModal />

          <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-slate-800 bg-white px-4 sm:px-6">
            <div className="flex items-center space-x-4">
              <button
                onClick={() => setMobileOpen(true)}
                className="flex h-10 w-10 items-center justify-center rounded-md text-slate-400 hover:bg-slate-950 hover:text-slate-100 lg:hidden"
                aria-label="Open navigation"
              >
                <Menu className="h-6 w-6" />
              </button>

              <div className="flex items-center space-x-2 text-sm text-slate-400">
                <span className="hidden sm:inline">PeoplePay360</span>
                <ChevronRight className="h-4 w-4 text-slate-600" />
                <span className="font-semibold text-slate-100">{currentSection?.name ?? 'Home'}</span>
              </div>
            </div>

            <div className="flex items-center space-x-3">
              <div className="relative hidden md:block">
                <Search className="absolute left-3 top-2.5 h-4 w-4 text-slate-500" />
                <input
                  type="search"
                  placeholder="Search..."
                  aria-label="Search"
                  className="h-9 w-64 rounded-md border border-slate-800 bg-slate-950 pl-9 pr-4 text-xs text-slate-200 placeholder:text-slate-500 focus:border-indigo-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/20"
                />
              </div>

              <Button variant="ghost" size="icon" className="relative text-slate-400 hover:text-slate-100" aria-label="Notifications">
                <Bell className="h-4 w-4" />
              </Button>
            </div>
          </header>

          <main className="flex-1 overflow-y-auto bg-slate-950 p-4 sm:p-6">
            <Outlet />
          </main>
        </div>
      </div>
    </ToastProvider>
  );
}
