import { useQuery } from '@tanstack/react-query';

import { fetchApi, queryString } from '@/lib/api-client';
import type { DashboardFilterParams, DashboardResponse } from '@/types/dashboard';

/**
 * The payroll dashboard (PS A7 / B9). One consolidated request per filter
 * change — the backend does the aggregation, this hook just puts the whole
 * filter set in the query key so TanStack Query refetches whenever any of
 * them change, and never quietly re-filters an already-loaded response on
 * the client (that would silently disagree with what the backend computed).
 */
export function useDashboard(filters: DashboardFilterParams) {
  return useQuery({
    queryKey: ['dashboard', 'summary', filters],
    queryFn: () =>
      fetchApi<DashboardResponse>(`/dashboard/summary${queryString({ ...filters })}`),
  });
}
