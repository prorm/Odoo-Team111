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
