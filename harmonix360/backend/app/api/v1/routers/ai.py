"""
AI Router — endpoints for standalone AI generation via Taskiq.

POST /api/v1/ai/generate  — enqueue AI call, return 202 + job_id
GET  /api/v1/ai/jobs/{id}  — poll job status/result

Architecture ref: Section 5 point 3 — "Routers enqueue a job (await task.kiq(...))
and return 202 Accepted with a job ID."
Hard Constraint #4 (Section 11) — "Do not call an AI provider synchronously
from a request handler — always enqueue via Taskiq."
"""
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from typing import Optional
from app.api.v1.deps import get_current_user, CurrentUser
from app.core.rate_limit import rate_limiter

router = APIRouter(prefix="/ai", tags=["AI"], dependencies=[Depends(rate_limiter)])


class AIGenerateRequest(BaseModel):
    prompt: str = Field(..., description="The prompt to send to the AI provider")
    context: dict = Field(default_factory=dict, description="Additional context dict")
    task_type: str = Field(default="general", description="Task type for cache TTL selection")


class AIJobResponse(BaseModel):
    job_id: str
    status: str = "queued"


class AIJobStatusResponse(BaseModel):
    job_id: str
    status: str  # "pending" | "completed" | "failed" | "ai_unavailable"
    result: Optional[dict] = None
    error: Optional[str] = None


@router.post("/generate", response_model=AIJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def generate_ai(
    dto: AIGenerateRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Enqueue an AI generation job. Returns 202 with the job_id.
    Poll GET /api/v1/ai/jobs/{job_id} for the result.
    """
    from app.jobs.tasks.ai_jobs import run_ai_generate

    task_handle = await run_ai_generate.kiq(
        prompt=dto.prompt,
        context=dto.context,
        task_type=dto.task_type,
    )
    return AIJobResponse(job_id=task_handle.task_id, status="queued")


@router.get("/jobs/{job_id}", response_model=AIJobStatusResponse)
async def get_ai_job_status(
    job_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Poll the status and result of an AI generation job."""
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

        # result.return_value is the dict from run_ai_generate
        data = result.return_value
        return AIJobStatusResponse(
            job_id=job_id,
            status=data.get("status", "completed"),
            result=data.get("result"),
            error=data.get("error"),
        )
    except Exception:
        # Result not ready yet
        return AIJobStatusResponse(job_id=job_id, status="pending")
