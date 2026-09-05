import { useQuery } from '@tanstack/react-query';

import { fetchApi, queryString } from '@/lib/api-client';
import type {
  AnomalyResponse,
  CalculationTree,
  ContractTimeline,
  FirewallReport,
  PayComparison,
} from '@/types/insights';

/**
 * Phase 10's read-side features. Every hook here is a `useQuery` over a GET —
 * there is no mutation in this file, because none of these features writes.
 *
 * The one action their screens offer is Revalidate, which is Phase 4's
 * existing `POST /payruns/{id}/validate` and lives in `usePayroll.ts` where it
 * always has. Re-exposing it here would create a second client-side path to a
 * payroll transition that already has one.
 */

/** The calculation tree behind one payslip (PRD §5.6). */
export function usePayslipCalculation(payslipId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ['insights', 'calculation', payslipId],
    queryFn: () => fetchApi<CalculationTree>(`/payslips/${payslipId}/calculation`),
    enabled: Boolean(payslipId) && enabled,
    // A payslip's lines are immutable once written — a run is recomputed,
    // which replaces them wholesale and changes the id set. Nothing to poll for.
    staleTime: 5 * 60 * 1000,
  });
}

/** This payslip against the employee's previous one (PRD §5.9). */
export function usePayComparison(payslipId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ['insights', 'comparison', payslipId],
    queryFn: () => fetchApi<PayComparison>(`/payslips/${payslipId}/comparison`),
    enabled: Boolean(payslipId) && enabled,
    staleTime: 5 * 60 * 1000,
  });
}

/** Contract history, optionally resolved against a period (PRD §5.8). */
export function useContractTimeline(
  employeeId: string | undefined,
  period?: { period_start?: string; period_end?: string },
) {
  return useQuery({
    queryKey: ['insights', 'timeline', employeeId, period],
    queryFn: () =>
      fetchApi<ContractTimeline>(
        `/employees/${employeeId}/contract-timeline${queryString({ ...period })}`,
      ),
    enabled: Boolean(employeeId),
  });
}

/** The grouped validation firewall for one payrun (PRD §5.10). */
export function usePayrunFirewall(payrunId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ['insights', 'firewall', payrunId],
    queryFn: () => fetchApi<FirewallReport>(`/payruns/${payrunId}/firewall`),
    enabled: Boolean(payrunId) && enabled,
  });
}

/** Deterministic anomalies for a period (PRD §5.7). */
export function useAnomalies(filters: {
  period_start?: string;
  period_end?: string;
  department_id?: string;
}) {
  return useQuery({
    queryKey: ['insights', 'anomalies', filters],
    queryFn: () => fetchApi<AnomalyResponse>(`/anomalies${queryString({ ...filters })}`),
  });
}
