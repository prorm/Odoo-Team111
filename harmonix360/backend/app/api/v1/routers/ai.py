"""AI Router — HR/payroll intelligence over the deterministic ERP.

Architecture §8.1 / §11 Hard Constraint #4: routers ENQUEUE, return `202` with a
job id, and the client polls. No handler here calls a provider synchronously.

    POST /api/v1/ai/ask                     -> 202 + job_id (contextual question)
    GET  /api/v1/ai/jobs/{id}               -> poll status/result
    POST /api/v1/ai/proposals               -> 202 + job_id (propose a mutation)
    GET  /api/v1/ai/proposals/{id}          -> read a pending proposal
    POST /api/v1/ai/proposals/{id}/confirm  -> execute it (synchronous, no AI)
    POST /api/v1/ai/proposals/{id}/reject   -> record a refusal
    POST /api/v1/ai/generate                -> the generic escape hatch

WHY CONFIRM IS SYNCHRONOUS WHILE EVERYTHING ELSE IS QUEUED
----------------------------------------------------------
The queue exists because provider calls are slow and external. Confirming a
proposal calls no provider at all — it runs the same service method the REST
route runs, and the caller needs its result (the created record, or the
validation error) in the response. Queueing it would hide a 409 behind a poll
and invite a client to retry a mutation it could not see the outcome of.

RBAC IS ENFORCED TWICE, ON PURPOSE
----------------------------------
Here, so an unauthorized question is refused immediately rather than after a
round trip through the queue; and again inside the worker, because a queued job
executes later and a job must not outlive the authorization that created it.
Both checks call the same `require_role`.
"""
from typing import Optional

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import CurrentUser, get_current_user, require_role
from app.core.database import get_db
from app.core.rate_limit import rate_limiter
from app.models.enums import HR_ROLES, PAYROLL_ROLES

router = APIRouter(prefix="/ai", tags=["AI"], dependencies=[Depends(rate_limiter)])


# ------------------------------------------------------------------ schemas


class AIGenerateRequest(BaseModel):
    prompt: str = Field(..., description="The prompt to send to the AI provider")
    context: dict = Field(default_factory=dict, description="Additional context dict")
    task_type: str = Field(default="general", description="Task type for cache TTL selection")


class AIAskRequest(BaseModel):
    """A question about real records.

    `task_type` selects which authoritative context gets assembled, and `params`
    names the records to scope it to. The question itself is never used to
    decide WHAT to load — a model choosing its own scope from free text is how
    an "explain this payslip" request quietly turns into a whole-department
    read.
    """

    question: str = Field(min_length=3, max_length=1000)
    task_type: str = Field(
        default="general",
        description=(
            "payslip_explanation | payroll_variance | pending_actions | "
            "anomaly_narration | general"
        ),
    )
    params: dict = Field(
        default_factory=dict,
        description=(
            "Scope: payslip_id, employee_id, payrun_id, department_id, "
            "period_start, period_end."
        ),
    )


class AIProposalRequest(BaseModel):
    """Ask the AI to prepare an action for human confirmation."""

    action: str = Field(default="create_time_off_request")
    question: str = Field(min_length=3, max_length=1000)
    params: dict = Field(default_factory=dict)


class ProposalDecision(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)


class AIJobResponse(BaseModel):
    job_id: str
    status: str = "queued"


class AIJobStatusResponse(BaseModel):
    job_id: str
    status: str  # "pending" | "completed" | "failed" | "ai_unavailable"
    result: Optional[dict] = None
    error: Optional[str] = None
    #: Set only when status == "ai_unavailable" — "rate_limited" |
    #: "not_configured" | "provider_error". Lets the UI show a distinct,
    #: honest state for a transient rate limit instead of a generic banner.
    reason: Optional[str] = None


# ------------------------------------------------------------------- routes


@router.post("/ask", response_model=AIJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def ask(
    dto: AIAskRequest,
    current_user: CurrentUser = Depends(require_role(HR_ROLES | PAYROLL_ROLES)),
):
    """Ask a contextual HR/payroll question about real records.

    The broad role gate here is the OUTER door: `context_assembly` applies the
    per-task-type gate (payroll questions are payroll roles only) against the
    same matrix, so an HR Manager who reaches this endpoint is still refused a
    payslip explanation.
    """
    from app.jobs.tasks.ai_jobs import run_hr_insight

    handle = await run_hr_insight.kiq(
        task_type=dto.task_type,
        question=dto.question,
        actor_email=current_user.email,
        params=dto.params,
    )
    return AIJobResponse(job_id=handle.task_id, status="queued")


@router.post(
    "/proposals", response_model=AIJobResponse, status_code=status.HTTP_202_ACCEPTED
)
async def propose_action(
    dto: AIProposalRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Have the AI investigate and PROPOSE an action. Nothing is written to the
    domain by this call.

    Deliberately gated by authentication only, not by a role list: the one
    proposable action is "create a time-off request", which Architecture §5
    grants to the Employee role for their own record. The service enforces
    whose record it may be, at execution — and this endpoint executes nothing.
    """
    from app.jobs.tasks.ai_jobs import run_ai_action_proposal

    handle = await run_ai_action_proposal.kiq(
        action=dto.action,
        params=dto.params,
        question=dto.question,
        actor_email=current_user.email,
    )
    return AIJobResponse(job_id=handle.task_id, status="queued")


@router.get("/proposals/{proposal_id}")
async def read_proposal(
    proposal_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Read a pending proposal so a human can decide on it."""
    from app.ai import proposals

    record = await proposals.load(proposal_id)
    if record["proposed_for_email"] != current_user.email.lower():
        # Same reasoning as `EmployeeService.assert_can_read`: a 403 would
        # confirm that this proposal id exists and who it belongs to.
        from fastapi import HTTPException

        raise HTTPException(404, f"Proposal '{proposal_id}' is not pending.")
    return record


@router.post("/proposals/{proposal_id}/confirm")
async def confirm_proposal(
    proposal_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Confirm a proposal. THIS is the call that mutates.

    Runs the same service the REST route and the MCP tool run, with this user's
    identity — so the validation, the version check, the allocation deduction
    and the audit row are the ones the product already has. A refusal from that
    service is returned unchanged.
    """
    from app.ai import proposals

    outcome = await proposals.execute(db, proposal_id, current_user)
    await db.commit()
    return outcome


@router.post("/proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: str,
    dto: ProposalDecision,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Decline a proposal. Nothing is executed; the refusal is audited."""
    from app.ai import proposals

    outcome = await proposals.reject(db, proposal_id, current_user, note=dto.note or "")
    await db.commit()
    return outcome


@router.post("/generate", response_model=AIJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate_ai(
    dto: AIGenerateRequest,
    current_user: CurrentUser = Depends(require_role(HR_ROLES | PAYROLL_ROLES)),
):
    """Enqueue a raw AI generation job — the generic escape hatch.

    Kept for the platform layer's own use. It carries no authoritative context
    and no ERP facts, so it cannot be used to get an "explanation" of payroll:
    whatever it answers with came from the caller's own prompt, not from the
    database. HR/payroll questions belong on `/ai/ask`.
    """
    from app.jobs.tasks.ai_jobs import run_ai_generate

    handle = await run_ai_generate.kiq(
        prompt=dto.prompt, context=dto.context, task_type=dto.task_type
    )
    return AIJobResponse(job_id=handle.task_id, status="queued")


@router.get("/jobs/{job_id}", response_model=AIJobStatusResponse)
async def get_ai_job_status(
    job_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Poll the status and result of an AI job."""
    from app.jobs.broker import result_backend

    try:
        result = await result_backend.get_result(job_id)

        if result.is_err:
            return AIJobStatusResponse(
                job_id=job_id,
                status="failed",
                error=str(result.error) if result.error else "Unknown error",
            )

        if result.return_value is None:
            return AIJobStatusResponse(job_id=job_id, status="pending")

        data = result.return_value
        return AIJobStatusResponse(
            job_id=job_id,
            status=data.get("status", "completed"),
            result=data.get("result"),
            error=data.get("error"),
            reason=data.get("reason"),
        )
    except Exception:
        # The result backend raises until the worker has written a result.
        return AIJobStatusResponse(job_id=job_id, status="pending")
