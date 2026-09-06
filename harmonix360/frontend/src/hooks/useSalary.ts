import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { fetchApi } from '@/lib/api-client';
import type { PaginatedResponse } from '@/types/common';
import type {
  SalaryRule,
  SalaryRuleInput,
  SalaryStructure,
  SalaryStructureInput,
} from '@/types/salary';

export function useSalaryRules() {
  return useQuery({
    queryKey: ['salary-rules'],
    queryFn: () => fetchApi<PaginatedResponse<SalaryRule>>('/salary-rules/?limit=200'),
  });
}

export function useSalaryStructuresAdmin() {
  return useQuery({
    queryKey: ['salary-structures'],
    queryFn: () =>
      fetchApi<PaginatedResponse<SalaryStructure>>('/salary-structures/?limit=200'),
  });
}

export function useSaveSalaryRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, values }: { id?: string; values: SalaryRuleInput }) =>
      id
        ? fetchApi<SalaryRule>(`/salary-rules/${id}`, {
            method: 'PATCH',
            body: JSON.stringify(values),
          })
        : fetchApi<SalaryRule>('/salary-rules/', {
            method: 'POST',
            body: JSON.stringify(values),
          }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['salary-rules'] });
      void queryClient.invalidateQueries({ queryKey: ['salary-structures'] });
    },
  });
}

export function useDeleteSalaryRule() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      fetchApi<SalaryRule>(`/salary-rules/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['salary-rules'] });
      void queryClient.invalidateQueries({ queryKey: ['salary-structures'] });
    },
  });
}

export function useSaveSalaryStructure() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, values }: { id?: string; values: SalaryStructureInput }) =>
      id
        ? fetchApi<SalaryStructure>(`/salary-structures/${id}`, {
            method: 'PATCH',
            body: JSON.stringify(values),
          })
        : fetchApi<SalaryStructure>('/salary-structures/', {
            method: 'POST',
            body: JSON.stringify(values),
          }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['salary-structures'] });
      void queryClient.invalidateQueries({ queryKey: ['payruns'] });
      void queryClient.invalidateQueries({ queryKey: ['contracts'] });
    },
  });
}

export function useDeleteSalaryStructure() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      fetchApi<SalaryStructure>(`/salary-structures/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['salary-structures'] });
      void queryClient.invalidateQueries({ queryKey: ['payruns'] });
      void queryClient.invalidateQueries({ queryKey: ['contracts'] });
    },
  });
}
