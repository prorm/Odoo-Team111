import { clearToken, getToken, setToken, tokenExpiresAt } from '@/lib/auth';

/**
 * Keeps an ACTIVE session alive, and ends an idle one honestly.
 *
 * THE PROBLEM THIS SOLVES. Access tokens live 15 minutes and nothing renewed
 * them, so a screen left open through a meeting failed on the next click —
 * during a payrun, with a red "Request failed (401)" and no way back but a
 * manual re-login. The short lifetime is worth keeping: it bounds what a stolen
 * token is worth. What was missing was renewal for the person still sitting
 * there.
 *
 * WHY PROACTIVE, NOT ON-401. Refreshing in response to a 401 cannot work: by
 * then the token is already expired, and `/auth/refresh` rightly refuses it. So
 * the refresh has to happen BEFORE expiry, which means a timer.
 *
 * The timer fires at REFRESH_AT of the remaining lifetime, so a 15-minute token
 * renews around minute 10 and there are two more chances before it dies. A
 * failed refresh is not fatal on its own — it retries at the next tick — and if
 * it never succeeds the token simply expires and `fetchApi` sends the user to
 * the login screen, which is the correct end state for an idle session.
 *
 * Closing the tab stops everything, which is the point: this extends a session
 * somebody is present for, not one they walked away from.
 */

/** Refresh once this fraction of the token's life remains. */
const REFRESH_AT = 0.35;
/** Never schedule further out than this, so a long-lived token still gets
 *  periodic liveness checks against the server. */
const MAX_DELAY_MS = 10 * 60 * 1000;
/** Never busy-loop if the clock is skewed or the token is already stale. */
const MIN_DELAY_MS = 15 * 1000;

let timer: ReturnType<typeof setTimeout> | null = null;
let running = false;

async function refreshNow(): Promise<boolean> {
  if (!getToken()) return false;
  try {
    const response = await fetch('/api/v1/auth/refresh', {
      method: 'POST',
      headers: { Authorization: `Bearer ${getToken()}` },
    });
    if (!response.ok) return false;
    const body = (await response.json()) as { access_token?: string };
    if (!body?.access_token) return false;
    setToken(body.access_token);
    return true;
  } catch {
    // Offline, or the server is down. Not a session problem — the existing
    // token is still valid until it isn't, and the next tick tries again.
    return false;
  }
}

function schedule(): void {
  if (timer) clearTimeout(timer);
  if (!running) return;

  const expiresAt = tokenExpiresAt();
  if (expiresAt === null) return;

  const remaining = expiresAt - Date.now();
  const delay = Math.min(
    MAX_DELAY_MS,
    Math.max(MIN_DELAY_MS, remaining * (1 - REFRESH_AT)),
  );

  timer = setTimeout(async () => {
    await refreshNow();
    schedule();
  }, delay);
}

/** Refresh immediately if the token is closer to expiry than one full cycle.
 *  A laptop that was asleep does not fire timers; coming back to the tab is the
 *  moment to find out whether the session survived. */
async function onWake(): Promise<void> {
  if (!running || document.visibilityState !== 'visible') return;
  const expiresAt = tokenExpiresAt();
  if (expiresAt === null) return;
  if (expiresAt - Date.now() < MAX_DELAY_MS) {
    await refreshNow();
    schedule();
  }
}

export function startSessionRefresh(): () => void {
  if (running) return stopSessionRefresh;
  running = true;
  schedule();
  document.addEventListener('visibilitychange', onWake);
  window.addEventListener('focus', onWake);
  return stopSessionRefresh;
}

export function stopSessionRefresh(): void {
  running = false;
  if (timer) clearTimeout(timer);
  timer = null;
  document.removeEventListener('visibilitychange', onWake);
  window.removeEventListener('focus', onWake);
}

/** Called by `fetchApi` when the server rejects the token outright. Ends the
 *  session once, from one place, so a page with eight concurrent queries does
 *  not stack up eight redirects. */
export function endSession(): void {
  stopSessionRefresh();
  clearToken();
  const here = `${window.location.pathname}${window.location.search}`;
  if (window.location.pathname !== '/login') {
    window.location.replace(`/login?expired=1&from=${encodeURIComponent(here)}`);
  }
}
