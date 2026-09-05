/**
 * AI assistant wire types (PS §5.1/§5.2).
 *
 * Every monetary value inside `facts` is a STRING, exactly as the backend
 * serialised it — Architecture §10. Nothing here parses one into a `number`:
 * JavaScript's `number` is a float, and `41800.00` surviving a round trip as
 * `41800` is the smallest possible way to start lying about someone's pay.
 * Amounts are rendered as received.
 *
 * `facts` is deliberately typed as an opaque record rather than modelled
 * field-by-field. Its shape is chosen per task type by the backend's context
 * builder, and mirroring that here would create a second definition of what an
 * explanation contains — one that goes stale the first time the builder learns
 * to investigate something new. The UI renders it structurally.
 */

/** Which authoritative context the backend assembles for a question. */
export const AI_TASK_TYPES = [
  'payslip_explanation',
  'payroll_variance',
  'pending_actions',
  'anomaly_narration',
  'general',
] as const;

export type AiTaskType = (typeof AI_TASK_TYPES)[number];

export interface AiAskRequest {
  question: string;
  task_type: AiTaskType;
  params: Record<string, string | boolean>;
}

export interface AiJobHandle {
  job_id: string;
  status: string;
}

/** The result body of a completed `/ai/ask` job. */
export interface AiInsight {
  answer: string | null;
  task_type: string;
  question: string;
  provider?: string;
  model?: string;
  cached?: boolean;
  latency_ms?: number;
  facts: Record<string, unknown>;
  unavailable_information: string[];
  fact_sources: string[];
  subject: Record<string, unknown>;
}

/**
 * `status` carries four outcomes the UI must tell apart:
 *   pending         — the worker has not answered yet, keep polling
 *   completed       — an answer, plus the facts behind it
 *   ai_unavailable  — no provider answered; the FACTS ARE STILL PRESENT and
 *                     must still be shown, with a banner instead of an error
 *   failed          — the request itself was rejected (403, bad params)
 */
export interface AiJobStatus {
  job_id: string;
  status: 'pending' | 'completed' | 'failed' | 'ai_unavailable' | string;
  result: AiInsight | null;
  error: string | null;
}

export interface AiProposal {
  proposal_id: string;
  action: string;
  params: Record<string, string>;
  summary: string;
  status: 'pending_review' | 'confirmed' | 'rejected' | string;
  proposed_for_email: string;
  rationale: string;
  ai_status: string;
  ai_provider: string;
  created_at: string;
  expires_in_seconds: number;
}

export interface AiProposalResult {
  proposal: AiProposal;
  ai_decision: string;
  ai_status: string;
  facts: Record<string, unknown>;
  unavailable_information: string[];
  fact_sources: string[];
  requires_human_confirmation: boolean;
  confirm_endpoint: string;
}

export interface AiProposalJobStatus {
  job_id: string;
  status: string;
  result: AiProposalResult | null;
  error: string | null;
}

export interface AiProposalOutcome extends AiProposal {
  result: Record<string, unknown> | null;
  confirmed_by?: string;
}

/** Labels for the question presets the assistant offers. */
export const AI_TASK_LABELS: Record<AiTaskType, string> = {
  payslip_explanation: 'Explain a payslip',
  payroll_variance: 'Department payroll variance',
  pending_actions: "What's blocking payroll",
  anomaly_narration: 'Unusual patterns',
  general: 'About an employee',
};
