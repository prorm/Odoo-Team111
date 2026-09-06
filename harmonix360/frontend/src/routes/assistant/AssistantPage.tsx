import * as React from 'react';
import { AlertTriangle, Bot, CheckCircle2, Clock, Database, Loader2, Send, ShieldQuestion, X } from 'lucide-react';

import { StatusMessage } from '@/components/StatusMessage';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Select } from '@/components/ui/select';
import { useAiAsk, useAiProposal } from '@/hooks/useAi';
import { useCurrentUser } from '@/hooks/useCurrentUser';
import { AI_TASK_LABELS, AI_TASK_TYPES, type AiTaskType } from '@/types/ai';
import { PAYROLL_ROLES, hasRole } from '@/types/enums';

/**
 * The AI assistant (PS §5.1).
 *
 * Two panels, and the split between them is the product's central claim:
 *
 *   ANSWER            what a language model wrote
 *   EVIDENCE          the authoritative ERP facts it was given, verbatim
 *
 * They are never merged. A payroll figure a manager acts on must be traceable
 * to the deterministic engine that produced it, and the fastest way to lose
 * that is a single pane where a narrated number and a computed number look
 * identical. Every amount under EVIDENCE is rendered as the exact string the
 * backend sent — no `Number()`, no locale formatting, no rounding.
 *
 * The screen stays useful when the model does not. If no provider answers, the
 * evidence panel still renders in full and the answer panel carries a banner —
 * because the evidence is the part that is true.
 */

interface ParamField {
  key: string;
  label: string;
  placeholder: string;
  required?: boolean;
  type?: string;
}

/** What each question needs in order to be scoped to real records. */
const TASK_FIELDS: Record<AiTaskType, ParamField[]> = {
  payslip_explanation: [
    { key: 'payslip_id', label: 'Payslip ID', placeholder: 'pslip_…', required: true },
  ],
  payroll_variance: [
    { key: 'department_id', label: 'Department ID (optional)', placeholder: 'dept_…' },
    { key: 'period_start', label: 'Period start', placeholder: '2026-08-01', type: 'date' },
    { key: 'period_end', label: 'Period end', placeholder: '2026-08-31', type: 'date' },
  ],
  pending_actions: [
    { key: 'payrun_id', label: 'Payrun ID (optional)', placeholder: 'prun_…' },
  ],
  anomaly_narration: [
    { key: 'period_start', label: 'Period start', placeholder: '2026-08-01', type: 'date' },
    { key: 'period_end', label: 'Period end', placeholder: '2026-08-31', type: 'date' },
  ],
  general: [
    { key: 'employee_id', label: 'Employee ID', placeholder: 'emp_…', required: true },
  ],
};

const SUGGESTED_QUESTION: Record<AiTaskType, string> = {
  payslip_explanation: 'Why did this salary change compared with last month?',
  payroll_variance: 'Why did this department’s payroll change this month?',
  pending_actions: 'What is blocking payroll right now?',
  anomaly_narration: 'Are there any unusual payroll or HR patterns I should know about?',
  general: 'What changed in this employee’s contract?',
};

/**
 * The heading over an "ai_unavailable" banner, chosen from `reason` rather
 * than a fixed string — a rate limit is worth retrying in a minute, a
 * missing key is not, and collapsing both into "AI unavailable" hides which
 * one a viewer is looking at.
 */
function unavailableHeading(reason: string | null | undefined): string {
  if (reason === 'rate_limited') return 'Temporarily unavailable — try again shortly';
  if (reason === 'not_configured') return 'AI not configured';
  return 'AI unavailable';
}

function titleCase(key: string): string {
  return key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

/**
 * Renders a fact section structurally, without knowing its shape.
 *
 * The context builder chooses what to include per question; a component that
 * hard-coded those field names would show nothing the first time the backend
 * learned to investigate something new. Values are printed as received — which
 * is also what keeps money exact.
 */
function FactNode({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined) {
    return <span className="text-slate-500 italic">not recorded</span>;
  }
  if (typeof value === 'boolean') {
    return <span className="text-slate-300">{value ? 'yes' : 'no'}</span>;
  }
  if (typeof value === 'string' || typeof value === 'number') {
    return <span className="text-slate-200 font-mono text-[13px]">{String(value)}</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-slate-500 italic">none</span>;
    return (
      <ul className="space-y-1">
        {value.slice(0, 25).map((item, index) => (
          <li key={index} className="border-l border-slate-800 pl-3">
            <FactNode value={item} depth={depth + 1} />
          </li>
        ))}
        {value.length > 25 && (
          <li className="text-slate-500 text-xs">…and {value.length - 25} more</li>
        )}
      </ul>
    );
  }
  const entries = Object.entries(value as Record<string, unknown>);
  return (
    <dl className="space-y-1">
      {entries.map(([key, child]) => (
        <div key={key} className="grid grid-cols-[minmax(9rem,auto)_1fr] gap-x-3 gap-y-0.5">
          <dt className="text-xs uppercase tracking-wide text-slate-500 pt-0.5">{titleCase(key)}</dt>
          <dd className="min-w-0 break-words">
            <FactNode value={child} depth={depth + 1} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

function EvidencePanel({
  facts,
  unavailable,
  sources,
}: {
  facts: Record<string, unknown>;
  unavailable: string[];
  sources: string[];
}) {
  const sections = Object.entries(facts);
  const [open, setOpen] = React.useState<string | null>(sections[0]?.[0] ?? null);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Database className="h-4 w-4 text-emerald-400" />
          Evidence — authoritative ERP data
        </CardTitle>
        <CardDescription>
          Calculated by the deterministic payroll engine. Amounts are shown exactly as stored.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {unavailable.length > 0 && (
          <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3">
            <p className="flex items-center gap-2 text-sm font-medium text-amber-200">
              <ShieldQuestion className="h-4 w-4" />
              Not established by the data
            </p>
            <ul className="mt-2 space-y-1 text-xs text-amber-100/80">
              {unavailable.map((note) => (
                <li key={note}>• {note}</li>
              ))}
            </ul>
          </div>
        )}

        {sections.length === 0 ? (
          <p className="text-sm text-slate-400">No facts were assembled for this question.</p>
        ) : (
          <div className="space-y-2">
            {sections.map(([name, value]) => (
              <div key={name} className="rounded-lg border border-slate-800">
                <button
                  type="button"
                  onClick={() => setOpen(open === name ? null : name)}
                  className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium text-slate-200 hover:bg-slate-800/50"
                >
                  {titleCase(name)}
                  <span className="text-xs text-slate-500">{open === name ? 'hide' : 'show'}</span>
                </button>
                {open === name && (
                  <div className="border-t border-slate-800 px-3 py-2 overflow-x-auto">
                    <FactNode value={value} />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}

        {sources.length > 0 && (
          <div>
            <p className="text-xs uppercase tracking-wide text-slate-500">Sources</p>
            <div className="mt-1 flex flex-wrap gap-1">
              {sources.map((source) => (
                <Badge key={source} variant="secondary" className="font-mono text-[10px]">
                  {source}
                </Badge>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function AskPanel() {
  const [taskType, setTaskType] = React.useState<AiTaskType>('pending_actions');
  const [question, setQuestion] = React.useState(SUGGESTED_QUESTION.pending_actions);
  const [params, setParams] = React.useState<Record<string, string>>({});
  const ai = useAiAsk();

  const fields = TASK_FIELDS[taskType];
  const missing = fields.filter((field) => field.required && !params[field.key]?.trim());

  function changeTask(next: AiTaskType) {
    setTaskType(next);
    setQuestion(SUGGESTED_QUESTION[next]);
    setParams({});
    ai.reset();
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (missing.length > 0) return;
    const cleaned = Object.fromEntries(
      Object.entries(params).filter(([, value]) => value.trim() !== ''),
    );
    void ai.ask({ question, task_type: taskType, params: cleaned });
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="space-y-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Ask about real records</CardTitle>
            <CardDescription>
              The assistant investigates connected HR and payroll data — contracts, attendance,
              time off and previous payslips — and explains what the engine calculated.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={submit} className="space-y-3">
              <div>
                <label className="text-xs uppercase tracking-wide text-slate-500">Question type</label>
                <Select
                  value={taskType}
                  onChange={(event) => changeTask(event.target.value as AiTaskType)}
                  className="mt-1"
                >
                  {AI_TASK_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {AI_TASK_LABELS[type]}
                    </option>
                  ))}
                </Select>
              </div>

              {fields.map((field) => (
                <div key={field.key}>
                  <label className="text-xs uppercase tracking-wide text-slate-500">
                    {field.label}
                  </label>
                  <Input
                    className="mt-1"
                    type={field.type ?? 'text'}
                    placeholder={field.placeholder}
                    value={params[field.key] ?? ''}
                    onChange={(event) =>
                      setParams((current) => ({ ...current, [field.key]: event.target.value }))
                    }
                  />
                </div>
              ))}

              <div>
                <label className="text-xs uppercase tracking-wide text-slate-500">Question</label>
                <Input
                  className="mt-1"
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                />
              </div>

              <Button type="submit" disabled={ai.sessionBusy || missing.length > 0}>
                {ai.phase === 'thinking' ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Investigating…
                  </>
                ) : (
                  <>
                    <Send className="mr-2 h-4 w-4" /> Ask
                  </>
                )}
              </Button>
              {missing.length > 0 && (
                <p className="text-xs text-slate-500">
                  {missing.map((field) => field.label).join(', ')} required — answers are scoped to a
                  specific record, never to the whole database.
                </p>
              )}
              {ai.sessionBusy && ai.phase !== 'thinking' && (
                <p className="text-xs text-slate-500">
                  Another AI request is running in this session — the assistant answers one
                  question at a time.
                </p>
              )}
            </form>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Bot className="h-4 w-4 text-indigo-400" />
              Answer
            </CardTitle>
            <CardDescription>
              Written by a language model from the evidence on the right. It never calculates
              payroll.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {ai.phase === 'idle' && (
              <p className="text-sm text-slate-400">
                Ask a question to see an explanation grounded in real records.
              </p>
            )}
            {ai.phase === 'thinking' && (
              <p className="flex items-center gap-2 text-sm text-slate-400">
                <Loader2 className="h-4 w-4 animate-spin" />
                Gathering authoritative facts and explaining them…
              </p>
            )}
            {ai.phase === 'blocked' && ai.notice && (
              <div className="rounded-lg border border-sky-500/40 bg-sky-500/10 p-3 text-sm text-sky-100">
                <p className="flex items-center gap-2 font-medium text-sky-200">
                  <Clock className="h-4 w-4" />
                  Please wait
                </p>
                <p className="mt-1 text-xs">{ai.notice}</p>
              </div>
            )}
            {(ai.phase === 'unavailable' || ai.phase === 'timeout') && ai.notice && (
              <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">
                <p className="flex items-center gap-2 font-medium text-amber-200">
                  <AlertTriangle className="h-4 w-4" />
                  {ai.phase === 'timeout' ? 'No response yet' : unavailableHeading(ai.reason)}
                </p>
                <p className="mt-1 text-xs">{ai.notice}</p>
              </div>
            )}
            {ai.phase === 'error' && ai.notice && <StatusMessage error={new Error(ai.notice)} />}
            {ai.phase === 'done' && ai.insight?.answer && (
              <div className="space-y-2">
                <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-200">
                  {ai.insight.answer}
                </p>
                <p className="text-xs text-slate-500">
                  {ai.insight.provider} · {ai.insight.model}
                  {ai.insight.cached ? ' · cached' : ''}
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {ai.insight ? (
        <EvidencePanel
          facts={ai.insight.facts}
          unavailable={ai.insight.unavailable_information}
          sources={ai.insight.fact_sources}
        />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Database className="h-4 w-4 text-emerald-400" />
              Evidence — authoritative ERP data
            </CardTitle>
            <CardDescription>
              Every figure in an answer comes from here, and can be checked against it.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-slate-400">
              The facts the assistant investigated will appear here alongside the answer.
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function ProposalPanel({ employeeId }: { employeeId: string | undefined }) {
  const [typeId, setTypeId] = React.useState('');
  const [from, setFrom] = React.useState('');
  const [to, setTo] = React.useState('');
  const flow = useAiProposal();

  const ready = Boolean(employeeId && typeId.trim() && from && to);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!ready || !employeeId) return;
    void flow.propose({
      action: 'create_time_off_request',
      question: `Request leave for me from ${from} to ${to}.`,
      params: {
        employee_id: employeeId,
        time_off_type_id: typeId.trim(),
        date_from: from,
        date_to: to,
        reason: 'Requested through the AI assistant',
      },
    });
  }

  if (!employeeId) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">AI-requested time off</CardTitle>
          <CardDescription>
            This login has no employee record attached, so there is nobody to request leave for.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">AI-requested time off</CardTitle>
        <CardDescription>
          The assistant checks your balance and prepares a request. Nothing is submitted until you
          confirm it.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <form onSubmit={submit} className="grid gap-3 sm:grid-cols-3">
          <div className="sm:col-span-3">
            <label className="text-xs uppercase tracking-wide text-slate-500">Leave type ID</label>
            <Input
              className="mt-1"
              placeholder="totype_…"
              value={typeId}
              onChange={(event) => setTypeId(event.target.value)}
            />
          </div>
          <div>
            <label className="text-xs uppercase tracking-wide text-slate-500">From</label>
            <Input className="mt-1" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div>
            <label className="text-xs uppercase tracking-wide text-slate-500">To</label>
            <Input className="mt-1" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
          </div>
          <div className="flex items-end">
            <Button type="submit" disabled={!ready || flow.sessionBusy} className="w-full">
              {flow.phase === 'thinking' ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Preparing…
                </>
              ) : (
                'Prepare request'
              )}
            </Button>
          </div>
        </form>

        {flow.phase === 'blocked' && flow.notice && (
          <div className="rounded-lg border border-sky-500/40 bg-sky-500/10 p-3 text-sm text-sky-100">
            <p className="flex items-center gap-2 font-medium text-sky-200">
              <Clock className="h-4 w-4" />
              Please wait
            </p>
            <p className="mt-1 text-xs">{flow.notice}</p>
          </div>
        )}
        {flow.phase === 'timeout' && flow.notice && (
          <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-100">
            <p className="flex items-center gap-2 font-medium text-amber-200">
              <AlertTriangle className="h-4 w-4" />
              No response yet
            </p>
            <p className="mt-1 text-xs">{flow.notice}</p>
          </div>
        )}
        {flow.phase === 'error' && flow.notice && <StatusMessage error={new Error(flow.notice)} />}

        {flow.proposal && (
          <div className="rounded-lg border border-indigo-500/40 bg-indigo-500/5 p-4">
            <div className="flex items-center justify-between gap-2">
              <p className="font-medium text-slate-100">Proposed — awaiting your confirmation</p>
              <div className="flex items-center gap-2">
                {flow.reason === 'rate_limited' && (
                  <Badge variant="secondary" className="gap-1">
                    <Clock className="h-3 w-3" /> AI busy
                  </Badge>
                )}
                <Badge variant="warning">pending review</Badge>
              </div>
            </div>
            <p className="mt-2 text-sm text-slate-200">{flow.proposal.proposal.summary}</p>
            <p className="mt-2 text-sm text-slate-400">{flow.proposal.proposal.rationale}</p>

            <div className="mt-3">
              <EvidencePanel
                facts={flow.proposal.facts}
                unavailable={flow.proposal.unavailable_information}
                sources={flow.proposal.fact_sources}
              />
            </div>

            {(flow.confirm.error || flow.reject.error) && (
              <div className="mt-3">
                <StatusMessage error={flow.confirm.error ?? flow.reject.error} />
              </div>
            )}

            <div className="mt-3 flex gap-2">
              <Button
                onClick={() => flow.confirm.mutate(flow.proposal!.proposal.proposal_id)}
                disabled={flow.confirm.isPending}
              >
                <CheckCircle2 className="mr-2 h-4 w-4" />
                Confirm and submit
              </Button>
              <Button
                variant="outline"
                onClick={() =>
                  flow.reject.mutate({
                    proposalId: flow.proposal!.proposal.proposal_id,
                    note: 'Declined in the assistant',
                  })
                }
                disabled={flow.reject.isPending}
              >
                <X className="mr-2 h-4 w-4" />
                Decline
              </Button>
            </div>
          </div>
        )}

        {flow.outcome && (
          <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-4 text-sm">
            <p className="font-medium text-emerald-200">
              {flow.outcome.status === 'confirmed'
                ? 'Submitted through the normal approval workflow.'
                : 'Proposal declined. Nothing was submitted.'}
            </p>
            {flow.outcome.result ? (
              <p className="mt-1 text-xs text-emerald-100/80">
                Request {String(flow.outcome.result.request_id)} · status{' '}
                {String(flow.outcome.result.status)}
              </p>
            ) : null}
            <Button variant="ghost" size="sm" className="mt-2" onClick={flow.reset}>
              Start another
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function AssistantPage() {
  const { data: user } = useCurrentUser();
  const canAskPayroll = hasRole(user?.role, PAYROLL_ROLES);

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Assistant</h1>
        <p className="mt-1 text-sm text-slate-400">
          Explanations and proposals over live records. The payroll engine remains the only
          authority for every amount — the assistant reads and explains, it never calculates.
        </p>
      </div>

      {canAskPayroll ? (
        <AskPanel />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Payroll questions need a payroll role</CardTitle>
            <CardDescription>
              Your role does not include payroll access, so the assistant will not read payroll
              records on your behalf. This is the same rule the rest of the product enforces.
            </CardDescription>
          </CardHeader>
        </Card>
      )}

      <ProposalPanel employeeId={user?.employee_id ?? undefined} />
    </div>
  );
}
