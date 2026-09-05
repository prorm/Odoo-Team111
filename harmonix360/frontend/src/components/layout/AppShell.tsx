import * as React from 'react';
import { Outlet, useLocation, Link } from 'react-router-dom';
import { Search, Bell, ChevronRight, Building2, Menu, X, LayoutGrid, StickyNote } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ToastProvider } from '@/components/ui/toast';
import { OfflineBanner } from '@/components/OfflineBanner';
import { ConflictModal } from '@/components/ConflictModal';

// A new Product Surface registers its nav entries here, e.g.:
// { name: 'Inventory', href: '/inventory', icon: Boxes }
const navItems: { name: string; href: string; icon: React.ComponentType<{ className?: string }> }[] = [
  { name: 'Notes', href: '/notes', icon: StickyNote },
];

export function AppShell() {
  const location = useLocation();
  const [mobileOpen, setMobileOpen] = React.useState(false);

  return (
    <ToastProvider>
      <div className="min-h-screen bg-slate-950 text-slate-100 flex overflow-hidden">
        {/* Sidebar backdrop for mobile */}
        {mobileOpen && (
          <div
            className="fixed inset-0 bg-slate-950/80 z-40 lg:hidden backdrop-blur-sm"
            onClick={() => setMobileOpen(false)}
          />
        )}

        {/* Sidebar Navigation */}
        <aside
          className={`fixed lg:static inset-y-0 left-0 z-50 w-64 bg-slate-900/95 border-r border-slate-800/80 flex flex-col transition-transform duration-300 ease-in-out ${
            mobileOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'
          }`}
        >
          {/* Sidebar Brand Header */}
          <div className="h-16 px-6 flex items-center justify-between border-b border-slate-800/80 bg-slate-950/40">
            <div className="flex items-center space-x-3">
              <div className="h-9 w-9 rounded-xl bg-gradient-to-tr from-indigo-600 to-indigo-400 flex items-center justify-center shadow-lg shadow-indigo-950/50">
                <LayoutGrid className="h-5 w-5 text-white" />
              </div>
              <div>
                <span className="font-bold text-lg text-slate-100 tracking-tight flex items-center gap-1.5">
                  Harmonix360
                  <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-indigo-950 text-indigo-400 border border-indigo-800/60">
                    v1.0
                  </span>
                </span>
                <p className="text-[11px] text-slate-400 flex items-center gap-1">
                  <Building2 className="h-3 w-3 text-slate-500" /> Tenant: tenant_demo
                </p>
              </div>
            </div>
            <button
              onClick={() => setMobileOpen(false)}
              className="lg:hidden text-slate-400 hover:text-white"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          {/* Navigation Links */}
          <div className="flex-1 py-6 px-3 space-y-1 overflow-y-auto">
            <div className="px-3 pb-2 text-[10px] font-semibold text-slate-400 uppercase tracking-wider">
              Product Surfaces
            </div>
            {navItems.length === 0 && (
              <p className="px-3 text-xs text-slate-500">
                No pages registered yet. Build a Product Surface and add it to `navItems`.
              </p>
            )}
            {navItems.map((item) => (
              <Link
                key={item.href}
                to={item.href}
                className={`flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors ${
                  location.pathname === item.href
                    ? 'bg-indigo-600/15 text-indigo-300 border border-indigo-500/30'
                    : 'text-slate-400 hover:bg-slate-800/60 hover:text-slate-100'
                }`}
              >
                <item.icon className="h-4 w-4" />
                {item.name}
              </Link>
            ))}
          </div>

          {/* User & Role Footer */}
          <div className="p-4 border-t border-slate-800/80 bg-slate-950/40 flex items-center justify-between">
            <div className="flex items-center space-x-3 overflow-hidden">
              <div className="h-8 w-8 rounded-full bg-slate-800 border border-slate-700 flex items-center justify-center text-slate-300 font-semibold text-xs flex-shrink-0">
                AD
              </div>
              <div className="truncate">
                <div className="text-xs font-medium text-slate-200 truncate">admin@harmonix360.com</div>
                <div className="flex items-center gap-1 mt-0.5">
                  <Badge variant="default" className="text-[9px] px-1.5 py-0">
                    ADMIN
                  </Badge>
                </div>
              </div>
            </div>
          </div>
        </aside>

        {/* Main Content Viewport */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
          <OfflineBanner />
          <ConflictModal />
          {/* Top Header */}
          <header className="h-16 px-6 bg-slate-900/80 border-b border-slate-800/80 flex items-center justify-between backdrop-blur-md sticky top-0 z-30">
            <div className="flex items-center space-x-4">
              <button
                onClick={() => setMobileOpen(true)}
                className="lg:hidden text-slate-400 hover:text-white"
              >
                <Menu className="h-6 w-6" />
              </button>

              <div className="flex items-center text-sm text-slate-400 space-x-2">
                <span>Harmonix360</span>
                <ChevronRight className="h-4 w-4 text-slate-600" />
                <span className="font-semibold text-slate-100">{location.pathname === '/' ? 'Dashboard' : location.pathname}</span>
              </div>
            </div>

            {/* Header Right Actions */}
            <div className="flex items-center space-x-3">
              <div className="relative hidden md:block">
                <Search className="h-4 w-4 absolute left-3 top-2.5 text-slate-500" />
                <input
                  type="text"
                  placeholder="Search..."
                  className="h-9 w-64 rounded-lg bg-slate-950/80 border border-slate-800 pl-9 pr-4 text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                />
              </div>

              <Button variant="ghost" size="icon" className="relative text-slate-400 hover:text-white">
                <Bell className="h-4 w-4" />
                <span className="absolute top-2 right-2 h-2 w-2 rounded-full bg-indigo-500" />
              </Button>

              <div className="h-4 w-px bg-slate-800" />

              <div className="hidden sm:flex items-center space-x-2 text-xs text-slate-400 font-mono">
                <span className="h-2 w-2 rounded-full bg-emerald-500" />
                <span>FastAPI Connected</span>
              </div>
            </div>
          </header>

          {/* Dynamic Route Content */}
          <main className="flex-1 overflow-y-auto p-6 lg:p-8 bg-slate-950">
            <Outlet />
          </main>
        </div>
      </div>
    </ToastProvider>
  );
}
