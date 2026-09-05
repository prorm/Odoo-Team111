import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { fetchApi } from '@/lib/api-client';
import type {
  AiAskRequest,
  AiInsight,
  AiJobHandle,
  AiJobStatus,
  AiProposalJobStatus,
  AiProposalOutcome,
  AiProposalResult,
  AiUnavailableReason,
} from '@/types/ai';

/**
 * AI jobs are asynchronous by architecture, not by accident.
 *
 * The backend enqueues every provider call through Taskiq and answers `202`
 * with a job id (Architecture §11, hard constraint #4), so there is no
 * request-response shape to wrap in a plain `useQuery`. These hooks own the
 * ask-then-poll cycle and expose it as one piece of state a component can
 * render directly.
 *
 * POLLING STOPS. Every loop below has a bounded attempt count and clears its
 * timer on unmount. A dropped worker must surface as "this is taking longer
 * than expected" rather than as a tab that quietly polls forever — that is
 * both a real cost and a way for a stalled backend to look healthy.
 */

const POLL_INTERVAL_MS = 800;
const MAX_POLLS = 75; // ~60 seconds, comfortably past the provider timeout.

export type AiPhase = 'idle' | 'thinking' | 'done' | 'unavailable' | 'error' | 'timeout' | 'blocked';

/**
 * ONE in-flight AI completion per browser tab, shared across both the Ask
 * panel and the Proposal panel.
 *
 * Groq is the sole provider now (Cerebras was removed 2026-09-06 — see
 * app/ai/provider_router.py) with an 8000 TPM ceiling and no fallback. The
 * two panels on the assistant page each own an independent hook instance, so
 * without this a person could fire a question AND a leave-request proposal
 * at the same time and burn the same per-minute budget twice for no reason.
 * A plain module boolean is enough — this is per-tab UI throttling, not
 * cross-tab or cross-user coordination, and `useSyncExternalStore` is what
 * lets two unrelated components re-render off the one shared flag.
 */
let aiRequestInFlight = false;
const inFlightListeners = new Set<() => void>();

function subscribeInFlight(listener: () => void): () => void {
  inFlightListeners.add(listener);
  return () => inFlightListeners.delete(listener);
}

function getInFlightSnapshot(): boolean {
  return aiRequestInFlight;
}

function setInFlight(value: boolean): void {
  if (aiRequestInFlight === value) return;
  aiRequestInFlight = value;
  inFlightListeners.forEach((listener) => listener());
}

const SESSION_BUSY_NOTICE =
  'Another AI request is already in progress in this session. Wait for it to finish, then try again.';

function useAiSessionBusy(): boolean {
  return useSyncExternalStore(subscribeInFlight, getInFlightSnapshot);
}

interface AskState {
  phase: AiPhase;
  insight: AiInsight | null;
  /** Present when the job finished without an answer — shown as a banner. */
  notice: string | null;
  /** Set only when phase is 'unavailable' — see AiUnavailableReason. */
  reason: AiUnavailableReason | null;
}

const IDLE: AskState = { phase: 'idle', insight: null, notice: null, reason: null };

function useCancellablePoll() {
  const timer = useRef<number | null>(null);
  const cancelled = useRef(false);

  useEffect(
    () => () => {
      cancelled.current = true;
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  const wait = useCallback(
    () =>
      new Promise<void>((resolve) => {
        timer.current = window.setTimeout(resolve, POLL_INTERVAL_MS);
      }),
    [],
  );

  return { wait, isCancelled: () => cancelled.current };
}

/** Ask a contextual HR/payroll question and poll until it resolves. */
export function useAiAsk() {
  const [state, setState] = useState<AskState>(IDLE);
  const { wait, isCancelled } = useCancellablePoll();
  const sessionBusy = useAiSessionBusy();

  const reset = useCallback(() => setState(IDLE), []);

  const ask = useCallback(
    async (request: AiAskRequest) => {
      // Checked and set synchronously, before the first `await` — nothing else
      // in this single-threaded tick can slip in between the check and the
      // claim, so this never races a second call started a moment later.
      if (aiRequestInFlight) {
        setState({ phase: 'blocked', insight: null, notice: SESSION_BUSY_NOTICE, reason: null });
        return;
      }
      setInFlight(true);
      setState({ phase: 'thinking', insight: null, notice: null, reason: null });
      try {
        const handle = await fetchApi<AiJobHandle>('/ai/ask', {
          method: 'POST',
          body: JSON.stringify(request),
        });

        for (let attempt = 0; attempt < MAX_POLLS; attempt += 1) {
          if (isCancelled()) return;
          await wait();
          if (isCancelled()) return;

          const job = await fetchApi<AiJobStatus>(`/ai/jobs/${handle.job_id}`);
          if (job.status === 'pending') continue;

          if (job.status === 'completed') {
            setState({ phase: 'done', insight: job.result, notice: null, reason: null });
            return;
          }
          if (job.status === 'ai_unavailable') {
            // The facts survive a provider outage and are the useful half.
            setState({
              phase: 'unavailable',
              insight: job.result,
              notice:
                job.error ??
                'No AI provider is available right now. The authoritative payroll data below is unaffected.',
              reason: job.reason ?? null,
            });
            return;
          }
          setState({
            phase: 'error',
            insight: null,
            notice: job.error ?? 'The assistant could not answer this question.',
            reason: null,
          });
          return;
        }

        setState({
          phase: 'timeout',
          insight: null,
          notice:
            'The assistant did not answer in time. The background worker may not be running — payroll itself is unaffected.',
          reason: null,
        });
      } catch (error) {
        setState({
          phase: 'error',
          insight: null,
          notice: error instanceof Error ? error.message : String(error),
          reason: null,
        });
      } finally {
        setInFlight(false);
      }
    },
    [wait, isCancelled],
  );

  return { ...state, ask, reset, sessionBusy };
}

interface ProposalState {
  phase: AiPhase;
  proposal: AiProposalResult | null;
  outcome: AiProposalOutcome | null;
  notice: string | null;
  /** Set only when phase is 'unavailable' — see AiUnavailableReason. */
  reason: AiUnavailableReason | null;
}

const PROPOSAL_IDLE: ProposalState = {
  phase: 'idle',
  proposal: null,
  outcome: null,
  notice: null,
  reason: null,
};

/**
 * The propose → confirm → execute cycle.
 *
 * `propose` never mutates anything on the server; `confirm` is the only call
 * that does, and it is a separate deliberate action. The two are kept as
 * distinct functions rather than one flow with a boolean precisely so a
 * component cannot accidentally chain them.
 */
export function useAiProposal() {
  const [state, setState] = useState<ProposalState>(PROPOSAL_IDLE);
  const { wait, isCancelled } = useCancellablePoll();
  const queryClient = useQueryClient();
  const sessionBusy = useAiSessionBusy();

  const reset = useCallback(() => setState(PROPOSAL_IDLE), []);

  const propose = useCallback(
    async (body: { action: string; question: string; params: Record<string, string> }) => {
      if (aiRequestInFlight) {
        setState({ ...PROPOSAL_IDLE, phase: 'blocked', notice: SESSION_BUSY_NOTICE });
        return;
      }
      setInFlight(true);
      setState({ ...PROPOSAL_IDLE, phase: 'thinking' });
      try {
        const handle = await fetchApi<AiJobHandle>('/ai/proposals', {
          method: 'POST',
          body: JSON.stringify(body),
        });

        for (let attempt = 0; attempt < MAX_POLLS; attempt += 1) {
          if (isCancelled()) return;
          await wait();
          if (isCancelled()) return;

          const job = await fetchApi<AiProposalJobStatus>(`/ai/jobs/${handle.job_id}`);
          if (job.status === 'pending') continue;
          if (job.status === 'completed' && job.result) {
            setState({
              phase: 'done',
              proposal: job.result,
              outcome: null,
              notice: null,
              reason: job.result.ai_reason ?? null,
            });
            return;
          }
          setState({
            ...PROPOSAL_IDLE,
            phase: 'error',
            notice: job.error ?? 'The assistant could not prepare a proposal.',
          });
          return;
        }
        setState({
          ...PROPOSAL_IDLE,
          phase: 'timeout',
          notice: 'The assistant did not respond in time. Nothing was submitted.',
        });
      } catch (error) {
        setState({
          ...PROPOSAL_IDLE,
          phase: 'error',
          notice: error instanceof Error ? error.message : String(error),
        });
      } finally {
        setInFlight(false);
      }
    },
    [wait, isCancelled],
  );

  const confirm = useMutation({
    mutationFn: (proposalId: string) =>
      fetchApi<AiProposalOutcome>(`/ai/proposals/${proposalId}/confirm`, { method: 'POST' }),
    onSuccess: (outcome) => {
      setState((current) => ({ ...current, outcome, proposal: null, notice: null }));
      // A confirmed leave request is a real row on the Time Off screen.
      queryClient.invalidateQueries({ queryKey: ['time-off'] });
    },
  });

  const reject = useMutation({
    mutationFn: ({ proposalId, note }: { proposalId: string; note: string }) =>
      fetchApi<AiProposalOutcome>(`/ai/proposals/${proposalId}/reject`, {
        method: 'POST',
        body: JSON.stringify({ note }),
      }),
    onSuccess: (outcome) => {
      setState((current) => ({ ...current, outcome, proposal: null, notice: null }));
    },
  });

  return { ...state, propose, confirm, reject, reset, sessionBusy };
}
