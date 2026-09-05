"""Taskiq tasks wrapping the AI layer.

Architecture §11, Hard Constraint #4: "Do not call an AI provider synchronously
from a request handler — always enqueue via Taskiq." A provider call is a
network round trip to a third party with a rate limit and a 30-second timeout;
holding a request handler open across it ties an API worker to someone else's
availability, and under a burst that is how the whole API becomes as slow as the
slowest provider. So routers enqueue, return 202 with a job id, and the client
polls.

THE CONTEXT IS BUILT IN THE WORKER, NOT IN THE ROUTER
-----------------------------------------------------
Assembling a payslip explanation touches a dozen queries. Doing that in the
handler and shipping the result through the queue would put the entire fact set
into a Redis payload, serialize every `Decimal` on the way, and make the enqueue
as expensive as the work. Instead the task receives identifiers, opens its own
session, and calls the same context builder — so the prompt is assembled once,
close to the database, from live authoritative state.

That also means the facts are read at execution time rather than at request
time, which is the correct freshness for a question like "what is blocking
payroll right now?".
"""
import logging

from app.ai.provider_router import AIUnavailableError, generate
from app.jobs.broker import broker

logger = logging.getLogger("harmonix360.jobs.ai")


@broker.task(task_name="run_ai_generate")
async def run_ai_generate(prompt: str, context: dict, task_type: str) -> dict:
    """Execute a raw AI generation call inside the Taskiq worker.

    Kept as-is from the platform foundation: it is the generic escape hatch and
    the AI decision node's sibling. HR/payroll questions should use
    `run_hr_insight` instead, which builds an authoritative context rather than
    trusting whatever dict the caller passed.
    """
    try:
        response = await generate(prompt=prompt, context=context, task_type=task_type)
        return {"status": "completed", "result": response.model_dump()}
    except AIUnavailableError as e:
        logger.warning("AI unavailable in task: %s", e)
        return {"status": "ai_unavailable", "result": None, "error": str(e)}
    except Exception as e:
        logger.exception("Unexpected error in AI task")
        return {"status": "failed", "result": None, "error": str(e)}


@broker.task(task_name="run_hr_insight")
async def run_hr_insight(
    task_type: str,
    question: str,
    actor_email: str,
    params: dict | None = None,
) -> dict:
    """Build an authoritative HR/payroll context and have a provider narrate it.

    Returns the model's answer AND the facts it was given. The facts are part of
    the result on purpose: an explanation of someone's pay that cannot be checked
    against its own inputs is not much better than a guess, and returning both
    lets the UI show "here is the answer, here is what it was based on".

    A provider failure returns `ai_unavailable` with the facts still attached —
    the deterministic half of the feature keeps working when the model does not,
    which is the whole reason the ERP is the source of truth.
    """
    from app.ai import context_assembly

    params = params or {}
    try:
        assembled = await context_assembly.assemble(
            task_type=task_type, question=question, actor_email=actor_email, params=params
        )
    except Exception as e:  # noqa: BLE001 - reported to the caller, not swallowed
        logger.exception("Failed to assemble AI context for task_type=%s", task_type)
        return {"status": "failed", "result": None, "error": str(e)}

    try:
        response = await generate(
            prompt=assembled["prompt"], context={}, task_type=task_type
        )
    except AIUnavailableError as e:
        logger.warning("AI unavailable for task_type=%s: %s", task_type, e)
        return {
            "status": "ai_unavailable",
            "result": {
                "answer": None,
                "task_type": task_type,
                "question": question,
                "facts": assembled["facts"],
                "unavailable_information": assembled["unavailable"],
                "fact_sources": assembled["sources"],
                "subject": assembled["subject"],
            },
            "error": (
                "No AI provider is currently available, so no narration could be produced. "
                "The authoritative ERP facts below were still retrieved and are unaffected."
            ),
        }
    except Exception as e:
        logger.exception("Unexpected error narrating task_type=%s", task_type)
        return {"status": "failed", "result": None, "error": str(e)}

    return {
        "status": "completed",
        "result": {
            "answer": response.text,
            "task_type": task_type,
            "question": question,
            "provider": response.provider,
            "model": response.model,
            "cached": response.cached,
            "latency_ms": response.latency_ms,
            "facts": assembled["facts"],
            "unavailable_information": assembled["unavailable"],
            "fact_sources": assembled["sources"],
            "subject": assembled["subject"],
        },
    }


@broker.task(task_name="run_ai_action_proposal")
async def run_ai_action_proposal(
    action: str,
    params: dict,
    question: str,
    actor_email: str,
) -> dict:
    """Investigate, then PROPOSE an action for a human to confirm.

    This task never mutates a domain record. Its only write is the
    `AI_PROPOSED_ACTION` audit row, and the pending proposal it parks in Redis.
    The actual service call happens in the confirm endpoint, after a person has
    said yes — see `app/ai/proposals.py`.
    """
    from app.ai import context_assembly

    try:
        return await context_assembly.propose(
            action=action, params=params, question=question, actor_email=actor_email
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("Failed to build AI proposal for action=%s", action)
        return {"status": "failed", "result": None, "error": str(e)}
