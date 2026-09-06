const TOKEN_KEY = 'access_token';

/**
 * Access-token storage.
 *
 * localStorage rather than an httpOnly cookie, matching the existing
 * `fetchApi` contract. That is a deliberate simplification for a single-tenant
 * internal tool, and it is worth naming what it costs: a token in localStorage
 * is readable by any script running on this origin, so an XSS bug becomes a
 * session compromise. The mitigations that matter here are the short token
 * lifetime (15 minutes) and the fact that authorization is enforced entirely
 * server-side — a stolen token grants exactly the role the server already
 * assigned, never more.
 */
export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    // Private browsing and blocked site-data both throw on access rather than
    // returning null.
    return null;
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Nothing useful to do — the session simply will not persist a reload.
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* see setToken */
  }
}

/**
 * When the current token expires, as epoch milliseconds — or null if there is
 * no token or it carries no readable `exp`.
 *
 * This decodes the JWT payload in the browser WITHOUT verifying it, which is
 * safe for exactly this use and nothing else: the answer is used to decide
 * *when to ask the server for a new token*, never to decide what the holder may
 * do. A forged `exp` buys an attacker a badly timed refresh request, which the
 * server then rejects on its own terms. Roles are still read from `/auth/me`
 * (see useCurrentUser) precisely because that is an authorization question and
 * this is not.
 */
export function tokenExpiresAt(): number | null {
  const token = getToken();
  if (!token) return null;
  const payload = token.split('.')[1];
  if (!payload) return null;
  try {
    // base64url -> base64, then pad. atob rejects the URL-safe alphabet.
    const normalised = payload.replace(/-/g, '+').replace(/_/g, '/');
    const padded = normalised.padEnd(Math.ceil(normalised.length / 4) * 4, '=');
    const exp = JSON.parse(atob(padded))?.exp;
    return typeof exp === 'number' ? exp * 1000 : null;
  } catch {
    return null;
  }
}
