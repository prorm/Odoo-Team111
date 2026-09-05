import { getToken } from '@/lib/auth';

const API_BASE = '/api/v1';

/**
 * An error carrying the HTTP status alongside the message.
 *
 * The status is what lets a form tell a 409 (this contract overlaps an existing
 * one — show it inline next to the dates, the user can fix it) from a 500 (the
 * server broke — a toast, and there is nothing the user can do). Throwing a
 * bare `Error` collapses that distinction and every failure ends up rendered
 * the same way.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }

  /** A conflict the user can resolve — an overlapping contract, a duplicate
   *  work email, a stale version. Always render these inline on the form. */
  get isConflict(): boolean {
    return this.status === 409;
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }
}

/** FastAPI's 422 shape, flattened into one readable sentence per field. */
interface ValidationDetail {
  loc: (string | number)[];
  msg: string;
}

function describeError(status: number, payload: unknown): string {
  if (payload && typeof payload === 'object' && 'detail' in payload) {
    const detail = (payload as { detail: unknown }).detail;

    if (typeof detail === 'string') return detail;

    if (Array.isArray(detail)) {
      // 422. `loc` starts with "body", which is noise to a person — the field
      // name is the last segment.
      return (detail as ValidationDetail[])
        .map((entry) => {
          const field = entry.loc?.[entry.loc.length - 1];
          return field && field !== 'body' ? `${field}: ${entry.msg}` : entry.msg;
        })
        .join('; ');
    }
  }

  // The version-conflict envelope from app/core/exceptions.py.
  if (payload && typeof payload === 'object' && 'error' in payload) {
    const error = (payload as { error?: { message?: string } }).error;
    if (error?.message) return error.message;
  }

  return `Request failed (${status})`;
}

export async function fetchApi<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  };

  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE}${endpoint}`, { ...options, headers });

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(response.status, describeError(response.status, payload), payload);
  }

  // 204 and friends have no body; calling .json() on one throws.
  if (response.status === 204 || response.headers.get('content-length') === '0') {
    return undefined as T;
  }

  return response.json();
}

/** Build a query string, dropping empty values so a cleared filter does not
 *  become `?search=` and match nothing. */
export function queryString(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined && value !== '') {
      search.set(key, String(value));
    }
  }
  const rendered = search.toString();
  return rendered ? `?${rendered}` : '';
}
