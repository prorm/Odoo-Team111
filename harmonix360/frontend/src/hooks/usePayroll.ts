import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { fetchApi, queryString } from '@/lib/api-client';
import type { PaginatedResponse } from '@/types/common';
import type {
  ComputeResult,
  DeliveryResult,
  EligibleEmployee,
  Payrun,
  Payslip,
  ValidationReport,
} from '@/types/payroll';

/**
 * Payroll data access (PS B5-B7).
 *
 * The one thing worth reading before using these: `Idempotency-Key` is
 * REQUIRED by the server on payrun create and compute (Architecture §6), and
 * it is generated HERE — once per user action, in the mutation — rather than
 * per render or per module. That placement is the whole point: a key created
 * on render would change under a double-click and defeat itself, and a key
 * created once per module would make every compute in the session look like a
 * replay of the first one.
 */

const PAYROLL_KEYS = ['payruns', 'payslips', 'payrun-validation'] as const;

function idempotencyKey(): Record<string, string> {
  // crypto.randomUUID is available in every browser this app targets; the
  // fallback keeps the dev server usable over plain http on older browsers,
  // where it is undefined.
  const id =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return { 'Idempotency-Key': id };
}

function useInvalidatePayroll() {
  const cache = useQueryClient();
  return () => Promise.all(PAYROLL_KEYS.map((key) => cache.invalidateQueries({ queryKey: [key] })));
}

export function usePayruns(offset = 0, status?: string) {
  return useQuery({
    queryKey: ['payruns', offset, status],
    queryFn: () =>
      fetchApi<PaginatedResponse<Payrun>>(
        `/payruns/${queryString({ limit: 50, offset, status })}`
      ),
  });
}

export function usePayrun(payrunId: string | undefined) {
  return useQuery({
    queryKey: ['payruns', payrunId],
    queryFn: () => fetchApi<Payrun>(`/payruns/${payrunId}`),
    enabled: Boolean(payrunId),
  });
}

export function usePayrunPayslips(payrunId: string | undefined) {
  return useQuery({
    queryKey: ['payslips', payrunId],
    queryFn: () => fetchApi<PaginatedResponse<Payslip>>(`/payruns/${payrunId}/payslips?limit=500`),
    enabled: Boolean(payrunId),
  });
}

export function usePayrunValidation(payrunId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ['payrun-validation', payrunId],
    queryFn: () => fetchApi<ValidationReport>(`/payruns/${payrunId}/validation`),
    enabled: Boolean(payrunId) && enabled,
  });
}

/** PS B5 step 2's candidates: employees with exactly one active contract
 *  covering the period. Disabled until both dates are set, because the
 *  question is meaningless without a period. */
export function useEligibleEmployees(periodStart: string, periodEnd: string) {
  return useQuery({
    queryKey: ['payruns', 'eligible', periodStart, periodEnd],
    queryFn: () =>
      fetchApi<PaginatedResponse<EligibleEmployee>>(
        `/payruns/eligible-employees${queryString({
          period_start: periodStart,
          period_end: periodEnd,
          limit: 500,
        })}`
      ),
    enabled: Boolean(periodStart && periodEnd && periodEnd >= periodStart),
  });
}

export function useSalaryStructures() {
  return useQuery({
    queryKey: ['salary-structures'],
    queryFn: () =>
      fetchApi<PaginatedResponse<{ id: string; name: string; code: string; rule_count: number }>>(
        '/salary-structures/?limit=200'
      ),
  });
}

export interface CreatePayrunInput {
  name: string;
  salary_structure_id: string;
  period_start: string;
  period_end: string;
  notes?: string | null;
  employee_ids: string[];
}

export function useCreatePayrun() {
  const invalidate = useInvalidatePayroll();
  return useMutation({
    mutationFn: (values: CreatePayrunInput) =>
      fetchApi<Payrun>('/payruns/', {
        method: 'POST',
        headers: idempotencyKey(),
        body: JSON.stringify(values),
      }),
    onSuccess: invalidate,
  });
}

export function useComputePayrun() {
  const invalidate = useInvalidatePayroll();
  return useMutation({
    mutationFn: ({ payrunId, version }: { payrunId: string; version: number }) =>
      fetchApi<ComputeResult>(`/payruns/${payrunId}/compute`, {
        method: 'POST',
        headers: idempotencyKey(),
        body: JSON.stringify({ version }),
      }),
    onSuccess: invalidate,
  });
}

/** Validate and Mark Paid carry no idempotency key: both are guarded by the
 *  payrun's `version`, so a repeat of either is already refused with a 409
 *  rather than applied twice. */
export function usePayrunTransition(action: 'validate' | 'mark-paid') {
  const invalidate = useInvalidatePayroll();
  return useMutation({
    mutationFn: ({ payrunId, version }: { payrunId: string; version: number }) =>
      fetchApi<ValidationReport | Payrun>(`/payruns/${payrunId}/${action}`, {
        method: 'POST',
        body: JSON.stringify({ version }),
      }),
    onSuccess: invalidate,
  });
}

export function useSendPayslips() {
  const invalidate = useInvalidatePayroll();
  return useMutation({
    mutationFn: ({ payrunId }: { payrunId: string }) =>
      fetchApi<DeliveryResult>(`/payruns/${payrunId}/send-payslips`, { method: 'POST' }),
    onSuccess: invalidate,
  });
}

export function useDeletePayrun() {
  const invalidate = useInvalidatePayroll();
  return useMutation({
    mutationFn: ({ payrunId, version }: { payrunId: string; version: number }) =>
      fetchApi<Payrun>(`/payruns/${payrunId}${queryString({ version })}`, { method: 'DELETE' }),
    onSuccess: invalidate,
  });
}
