import { useMutation, useQuery } from '@tanstack/react-query';
import { ApiError, fetchApi } from '@/lib/api-client';
import { getToken } from '@/lib/auth';

async function documentResponse(path: string): Promise<Response> {
  const response = await fetch(`/api/v1${path}`, {
    headers: { Authorization: `Bearer ${getToken()}` },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    throw new ApiError(response.status, typeof detail === 'string' ? detail : detail?.message ?? 'Unable to load payslip', body);
  }
  return response;
}

export function usePayslipPreview(id: string) {
  return useQuery({
    queryKey: ['payroll', 'document', id],
    queryFn: async () => (await documentResponse(`/payslips/${id}/preview`)).text(),
  });
}

export function usePrintPayslip(id: string) {
  return useMutation({
    mutationFn: async () => {
      const blob = await (await documentResponse(`/payslips/${id}/pdf`)).blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = `payslip-${id}.pdf`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    },
  });
}

export interface DeliveryStatus {
  payslip_id: string;
  status: 'pending' | 'sent' | 'failed';
  queued: boolean;
  error: string | null;
  attempts: number;
  sent_at: string | null;
}

export function useDeliveryStatuses(payrunId: string | undefined, enabled: boolean) {
  return useQuery({
    queryKey: ['payroll', 'deliveries', payrunId],
    queryFn: () => fetchApi<{ items: DeliveryStatus[] }>(`/payruns/${payrunId}/deliveries`),
    enabled: !!payrunId && enabled,
    refetchInterval: 1500,
  });
}
