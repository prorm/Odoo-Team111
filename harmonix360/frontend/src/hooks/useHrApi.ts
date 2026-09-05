import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { fetchApi, queryString } from '@/lib/api-client';
import type { PaginatedResponse } from '@/types/common';
import type {
  Contract,
  ContractInput,
  Employee,
  EmployeeInput,
  EmployeeRef,
  SmartButtonCounts,
  WorkingSchedule,
  WorkingScheduleType,
  ScheduleLineInput,
} from '@/types/hr';

/**
 * Query hooks for the Phase 1 HR entities.
 *
 * Query keys are arrays whose first element names the entity, so a mutation can
 * invalidate everything for that entity with one prefix
 * (`['employees']`) without listing every filter combination it might have
 * cached. Getting that wrong is what leaves a list showing a row the user just
 * deleted.
 */

// --------------------------------------------------------------- employees

export interface EmployeeFilters {
  search?: string;
  department_id?: string;
  status?: string;
  employee_type?: string;
  limit?: number;
  offset?: number;
}

export function useEmployees(filters: EmployeeFilters = {}) {
  return useQuery({
    queryKey: ['employees', filters],
    queryFn: () =>
      fetchApi<PaginatedResponse<Employee>>(`/employees/${queryString({ ...filters })}`),
  });
}

export function useEmployee(id: string | undefined) {
  return useQuery({
    queryKey: ['employees', 'detail', id],
    queryFn: () => fetchApi<Employee>(`/employees/${id}`),
    enabled: Boolean(id),
  });
}

export function useSmartButtonCounts(id: string | undefined) {
  return useQuery({
    queryKey: ['employees', 'counts', id],
    queryFn: () => fetchApi<SmartButtonCounts>(`/employees/${id}/counts`),
    enabled: Boolean(id),
  });
}

export function useManagerOptions(search: string, excludeId?: string) {
  return useQuery({
    queryKey: ['employees', 'lookup', search, excludeId],
    queryFn: () =>
      fetchApi<EmployeeRef[]>(`/employees/lookup${queryString({ search, exclude: excludeId })}`),
    // The picker is a search-as-you-type field; re-fetching the same term on
    // every keystroke round-trip would be wasteful for a list that barely moves.
    staleTime: 30_000,
  });
}

export function useSaveEmployee() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, values }: { id?: string; values: EmployeeInput }) =>
      id
        ? fetchApi<Employee>(`/employees/${id}`, { method: 'PATCH', body: JSON.stringify(values) })
        : fetchApi<Employee>('/employees/', { method: 'POST', body: JSON.stringify(values) }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['employees'] }),
  });
}

export function useDeleteEmployee() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => fetchApi<Employee>(`/employees/${id}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['employees'] }),
  });
}

// ---------------------------------------------------------------- schedules

export function useWorkingSchedules() {
  return useQuery({
    queryKey: ['working-schedules'],
    queryFn: () => fetchApi<PaginatedResponse<WorkingSchedule>>('/working-schedules/?limit=200'),
  });
}

export interface WorkingScheduleInput {
  name: string;
  schedule_type: WorkingScheduleType;
  lines: ScheduleLineInput[];
}

export function useSaveWorkingSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, values }: { id?: string; values: WorkingScheduleInput }) =>
      id
        ? fetchApi<WorkingSchedule>(`/working-schedules/${id}`, {
            method: 'PATCH',
            body: JSON.stringify(values),
          })
        : fetchApi<WorkingSchedule>('/working-schedules/', {
            method: 'POST',
            body: JSON.stringify(values),
          }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['working-schedules'] });
      // An employee row embeds its schedule's name and weekly hours, so a
      // renamed or re-timed schedule leaves those stale.
      queryClient.invalidateQueries({ queryKey: ['employees'] });
    },
  });
}

export function useDeleteWorkingSchedule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => fetchApi<WorkingSchedule>(`/working-schedules/${id}`, { method: 'DELETE' }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['working-schedules'] }),
  });
}

// ---------------------------------------------------------------- contracts

export interface ContractFilters {
  employee_id?: string;
  status?: string;
  limit?: number;
  offset?: number;
}

export function useContracts(filters: ContractFilters = {}) {
  return useQuery({
    queryKey: ['contracts', filters],
    queryFn: () => fetchApi<PaginatedResponse<Contract>>(`/contracts/${queryString({ ...filters })}`),
  });
}

export function useSaveContract() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, values }: { id?: string; values: ContractInput }) =>
      id
        ? fetchApi<Contract>(`/contracts/${id}`, { method: 'PATCH', body: JSON.stringify(values) })
        : fetchApi<Contract>('/contracts/', { method: 'POST', body: JSON.stringify(values) }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['contracts'] });
      // The Employee form's contract smart-button count just moved.
      queryClient.invalidateQueries({ queryKey: ['employees', 'counts'] });
    },
    // Deliberately NO onError toast. A 409 here is an expected outcome — HR
    // renewing a contract without ending the previous one will hit it
    // routinely — and it belongs inline on the form next to the dates that
    // caused it, not in a transient notification that disappears while the
    // user is still reading the form.
  });
}

export function useDeleteContract() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => fetchApi<Contract>(`/contracts/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['contracts'] });
      queryClient.invalidateQueries({ queryKey: ['employees', 'counts'] });
    },
  });
}
