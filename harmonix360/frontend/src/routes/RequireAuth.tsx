import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';

import { getToken } from '@/lib/auth';
import { startSessionRefresh, stopSessionRefresh } from '@/lib/session';

/**
 * Sends anyone without a token to the login screen.
 *
 * This is NAVIGATION, not authorization. The backend keeps a non-production
 * convenience where an unauthenticated request is treated as the demo admin
 * (app/api/v1/deps.py) so `curl` and Swagger work without a login round-trip —
 * which means that without this gate the UI would silently run as an admin
 * regardless of who is sitting at it, and the role-filtered navigation would
 * always show everything. Requiring a token client-side makes the five roles
 * actually observable in the app.
 *
 * The real enforcement is unchanged and unaffected: every endpoint behind here
 * is independently guarded by `require_role`, and deleting this component would
 * not grant anyone a single extra permission.
 */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const location = useLocation();
  const signedIn = Boolean(getToken());

  // The sliding session runs for exactly as long as the user is inside the
  // authenticated app: started here, stopped on unmount. See lib/session.ts.
  React.useEffect(() => {
    if (!signedIn) return;
    startSessionRefresh();
    return stopSessionRefresh;
  }, [signedIn]);

  if (!signedIn) {
    // `state` carries where they were headed, so a deep link survives the
    // detour through login rather than dumping everyone on the same page.
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return <>{children}</>;
}
