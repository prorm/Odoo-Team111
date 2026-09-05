"""AI-initiated mutations: propose -> human confirms -> the existing service runs.

Architecture §8.1: "an AI decision node proposes an action with a written
rationale, sets state to PENDING_REVIEW, and a human must explicitly confirm
before the identical authorized service method actually runs. The proposal, the
AI's raw output, and the human confirmation are all written to the audit log."

THE RULE THIS MODULE EXISTS TO ENFORCE
--------------------------------------
Inferring intent is not consent. "Request casual leave for Rahul next Friday"
is a sentence, not an authorization — the model may have misread the name, the
date, or the leave type, and the person who would find out is the one whose pay
changes. So the model's output here can only ever become a PROPOSAL. The write
happens in `execute()`, which runs only when a human, identified separately,
confirms that specific proposal by id.

WHERE A PROPOSAL LIVES, AND WHY THAT IS NOT A PARALLEL AUDIT SYSTEM
-------------------------------------------------------------------
The durable record is the AUDIT LOG — `AI_PROPOSED_ACTION`, then
`AI_CONFIRMED_ACTION` or `AI_REJECTED_ACTION`, then whatever row the service
itself writes when it runs. That chain answers §11's question ("who proposed,
who confirmed, what executed, what happened") from the same table every human
action is recorded in, with no second system to reconcile.

Redis holds only the pending proposal between those two audit rows: the
parameters, the rationale, and who may confirm. It is a short-lived handoff, not
a record. If it expires, nothing is lost that the audit log does not already
have — the human simply asks again, which is the correct outcome for a stale
proposal about next Friday.

WHAT MAY BE PROPOSED
--------------------
`_EXECUTORS` is a closed registry. An action reachable this way must be one
whose service does its own authorization and validation, and whose blast radius
is a single reversible HR record. Payroll compute is deliberately absent, and so
is anything that writes money.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, Callable, Optional

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import CurrentUser
from app.audit.logger import AuditLogger
from app.core.redis import redis_client
from app.mcp import tools
from app.mcp.identity import resolve_actor

logger = logging.getLogger("harmonix360.ai.proposals")

#: How long a pending proposal stays confirmable. Long enough for a person to
#: read it and decide; short enough that "approve the thing from yesterday"
#: cannot happen against facts that have since changed. On expiry the answer is
#: 404 and the human asks again — deliberately, because re-asking re-reads the
#: balance and the calendar.
PROPOSAL_TTL_SECONDS = 900

_KEY_PREFIX = "ai_proposal:"

#: Proposal lifecycle. `pending_review` is Architecture §8.1's PENDING_REVIEW.
PENDING = "pending_review"
CONFIRMED = "confirmed"
REJECTED = "rejected"
FAILED = "failed"


async def _execute_create_time_off_request(
    session: AsyncSession, params: dict, actor_email: str
) -> dict:
    """The one path a confirmed leave proposal takes — the same MCP tool an
    agent would call directly, which is the same service the REST route calls."""
    return await tools.create_time_off_request(
        session,
        employee_id=params["employee_id"],
        time_off_type_id=params["time_off_type_id"],
        date_from=params["date_from"],
        date_to=params["date_to"],
        actor_email=actor_email,
        reason=params.get("reason", ""),
    )


#: action kind -> executor. Closed by design; see the module docstring.
_EXECUTORS: dict[str, Callable] = {
    "create_time_off_request": _execute_create_time_off_request,
}

#: Human-readable summary per action kind, used in the audit row and the UI.
_SUMMARIES: dict[str, Callable[[dict], str]] = {
    "create_time_off_request": lambda p: (
        f"Create a time-off request for employee {p['employee_id']} "
        f"({p['date_from']} to {p['date_to']}, type {p['time_off_type_id']})"
    ),
}

KNOWN_ACTIONS = frozenset(_EXECUTORS)


def _key(proposal_id: str) -> str:
    return f"{_KEY_PREFIX}{proposal_id}"


async def create(
    session: AsyncSession,
    *,
    action: str,
    params: dict,
    proposed_for_email: str,
    rationale: str,
    ai_status: str,
    ai_provider: str,
    raw_output: Optional[str],
    context_facts: Optional[dict] = None,
) -> dict:
    """Record a proposal and park it for human review.

    `proposed_for_email` is the user the action would be performed AS, and the
    only identity allowed to confirm it later. Binding it at proposal time
    closes the obvious hole: without it, anyone who learned a proposal id could
    confirm a mutation that would then run under someone else's rights.

    The caller must commit — this writes an audit row through the caller's
    session, deliberately, so the proposal and its audit entry land together or
    not at all.
    """
    if action not in _EXECUTORS:
        raise HTTPException(
            400,
            f"'{action}' is not an action the AI layer may propose. "
            f"Proposable actions: {sorted(KNOWN_ACTIONS)}.",
        )

    proposal_id = f"prop_{uuid.uuid4().hex[:16]}"
    summary = _SUMMARIES[action](params)
    record = {
        "proposal_id": proposal_id,
        "action": action,
        "params": params,
        "summary": summary,
        "status": PENDING,
        "proposed_for_email": proposed_for_email.lower(),
        "rationale": rationale,
        "ai_status": ai_status,
        "ai_provider": ai_provider,
        "created_at": datetime.now(UTC).isoformat(),
        "expires_in_seconds": PROPOSAL_TTL_SECONDS,
    }

    await redis_client.set(_key(proposal_id), json.dumps(record), ex=PROPOSAL_TTL_SECONDS)

    # The durable record. `raw_output` is kept because §11 asks for what the AI
    # actually said, not a cleaned-up version of it — if a proposal is later
    # disputed, the paraphrase is not the evidence.
    await AuditLogger.log_mutation(
        session=session,
        actor="ai-agent",
        action="AI_PROPOSED_ACTION",
        entity="AIProposal",
        entity_id=proposal_id,
        after_diff={
            "proposed_action": action,
            "params": params,
            "summary": summary,
            "status": PENDING,
            "proposed_for": proposed_for_email,
            "ai_status": ai_status,
            "ai_provider": ai_provider,
            "ai_raw_output": (raw_output or "")[:4000],
            "context_fact_keys": sorted(context_facts or {}),
        },
        reason=rationale,
    )
    return record


async def load(proposal_id: str) -> dict:
    raw = await redis_client.get(_key(proposal_id))
    if not raw:
        raise HTTPException(
            404,
            f"Proposal '{proposal_id}' is not pending. It was already decided, or it "
            f"expired after {PROPOSAL_TTL_SECONDS // 60} minutes. Ask again to get a "
            "fresh proposal built on current data.",
        )
    return json.loads(raw)


async def reject(
    session: AsyncSession, proposal_id: str, user: CurrentUser, *, note: str = ""
) -> dict:
    """Record a human refusal. Nothing is executed and nothing is written to
    the domain — but the refusal itself is audited, because "the AI suggested
    this and a person said no" is exactly as informative as a yes."""
    record = await load(proposal_id)
    _assert_confirmer(record, user)

    await AuditLogger.log_mutation(
        session=session,
        actor=user.email,
        action="AI_REJECTED_ACTION",
        entity="AIProposal",
        entity_id=proposal_id,
        before_diff={"status": PENDING},
        after_diff={"status": REJECTED, "proposed_action": record["action"]},
        reason=note or "Rejected by human reviewer",
    )
    await redis_client.delete(_key(proposal_id))
    return {**record, "status": REJECTED, "result": None}


def _assert_confirmer(record: dict, user: CurrentUser) -> None:
    """Only the user the proposal was built for may confirm it.

    The role check that decides whether the action is permitted happens INSIDE
    the service the executor calls, on this same user — so this is not the
    authorization, it is the binding between a proposal and a principal.
    """
    if record["proposed_for_email"] != user.email.lower():
        raise HTTPException(
            403,
            "This proposal was prepared for a different user and can only be confirmed "
            "by them. Ask for a new proposal under your own login.",
        )


async def execute(
    session: AsyncSession, proposal_id: str, user: CurrentUser
) -> dict:
    """Confirm a proposal and run it through the existing service.

    The mutation happens here and nowhere else. Note what this function does NOT
    do: it does not re-read the AI's rationale, does not re-run the model, and
    does not adjust the parameters. It executes exactly what the human saw and
    approved — an "improved" version of a confirmed action is an unconfirmed
    action.

    A validation failure inside the service is left to propagate. The whole
    point of the single-path property is that an AI-initiated leave request with
    an insufficient balance fails identically to a hand-typed one; catching that
    here and returning a friendlier envelope would be the first step toward a
    second, softer rulebook.
    """
    record = await load(proposal_id)
    _assert_confirmer(record, user)
    executor = _EXECUTORS[record["action"]]

    # Consume the pending proposal BEFORE running, so a double-confirm cannot
    # execute twice. A failure below is reported and audited; the human asks
    # again rather than retrying a proposal whose outcome is now ambiguous.
    await redis_client.delete(_key(proposal_id))

    await AuditLogger.log_mutation(
        session=session,
        actor=user.email,
        action="AI_CONFIRMED_ACTION",
        entity="AIProposal",
        entity_id=proposal_id,
        before_diff={"status": PENDING},
        after_diff={
            "status": CONFIRMED,
            "proposed_action": record["action"],
            "params": record["params"],
            "confirmed_by": user.email,
            "confirmed_role": user.role.value,
        },
        reason=record["rationale"],
    )

    try:
        result = await executor(session, record["params"], user.email)
    except HTTPException as exc:
        # The service refused. Audit the outcome so the trail shows a confirmed
        # proposal that did not take effect, then re-raise the service's own
        # status and message unchanged.
        await AuditLogger.log_mutation(
            session=session,
            actor=user.email,
            action="AI_ACTION_REJECTED_BY_VALIDATION",
            entity="AIProposal",
            entity_id=proposal_id,
            after_diff={
                "status": FAILED,
                "proposed_action": record["action"],
                "status_code": exc.status_code,
                "detail": str(exc.detail)[:2000],
            },
        )
        raise

    await AuditLogger.log_mutation(
        session=session,
        actor=user.email,
        action="AI_ACTION_EXECUTED",
        entity="AIProposal",
        entity_id=proposal_id,
        after_diff={
            "status": CONFIRMED,
            "proposed_action": record["action"],
            "result": _audit_safe(result),
        },
    )
    logger.info(
        "AI proposal %s executed by %s: %s", proposal_id, user.email, record["summary"]
    )
    return {**record, "status": CONFIRMED, "result": result, "confirmed_by": user.email}


def _audit_safe(value: Any) -> Any:
    """Audit diffs are JSONB. Everything a tool returns is already JSON-safe
    (money as strings), but this guards the one thing that would raise on
    insert rather than surfacing later."""
    try:
        json.dumps(value)
        return value
    except TypeError:  # pragma: no cover - defensive
        return {"repr": repr(value)[:2000]}


async def resolve_confirming_user(session: AsyncSession, email: str) -> CurrentUser:
    """The human confirming, resolved the same way an MCP actor is."""
    return await resolve_actor(session, email)


__all__ = [
    "CONFIRMED",
    "FAILED",
    "KNOWN_ACTIONS",
    "PENDING",
    "PROPOSAL_TTL_SECONDS",
    "REJECTED",
    "create",
    "execute",
    "load",
    "reject",
]
