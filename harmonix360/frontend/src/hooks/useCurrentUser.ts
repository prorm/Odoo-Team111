import { useQuery } from '@tanstack/react-query';

import { fetchApi } from '@/lib/api-client';
import type { UserResponse } from '@/types/user';

/**
 * The authenticated user, as the SERVER reports them.
 *
 * Deliberately not decoded from the JWT in the browser. A token is readable
 * and editable by whoever holds it, so a role read out of one client-side is a
 * role the user chose. Asking the server costs one request and makes the
 * answer the same one every endpoint will enforce.
 *
 * Used for presentation: which nav entries to render, which actions to offer.
 * Never for authorization — that is `require_role` server-side, on every call.
 */
export function useCurrentUser() {
  return useQuery({
    queryKey: ['current-user'],
    queryFn: () => fetchApi<UserResponse>('/auth/me'),
    // The role does not change mid-session, and re-fetching it on every window
    // focus would put a request behind every tab switch for no new information.
    staleTime: 5 * 60 * 1000,
    retry: false,
  });
}
