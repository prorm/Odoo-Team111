"""Dispatch: question -> the right context -> a rendered prompt.

The thin layer between a Taskiq task and `context_builder`. It opens a session,
re-checks who is asking, picks the context appropriate to the task type, and
renders it through `app/ai/prompts`.

AUTHORIZATION IS RE-APPLIED HERE, NOT ASSUMED
---------------------------------------------
The router already ran `require_role` before enqueuing. This runs it again,
inside the worker, against the same `require_role`. That is not redundant
belt-and-braces for its own sake: a queued job is a request that executes later,
in a different process, and possibly after the asking user's rights have
changed. The facts assembled here include payroll amounts, so the check that
gates them belongs next to the code that reads them — a job that outlives an
authorization is exactly the kind of gap §9 is written to close.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import select

from app.ai import context_builder, prompts
from app.ai.decision_nodes import AIDecisionNode
from app.core.database import AsyncSessionLocal
from app.models.enums import HR_ROLES, PAYROLL_ROLES
from app.models.payroll import Payslip
from app.mcp.identity import actor_with_role, resolve_actor
from app.repositories.hr import EmployeeRepository

logger = logging.getLogger("harmonix360.ai.assembly")

#: Which roles may ask each kind of question. Payroll questions are payroll
#: roles only — an HR Manager has zero payroll access under Architecture §5, and
#: routing that person's question through an AI narrator would be a disclosure
#: path around the matrix rather than a feature.
_TASK_ROLES = {
    "payslip_explanation": PAYROLL_ROLES,
    "payroll_variance": PAYROLL_ROLES,
    "pending_actions": PAYROLL_ROLES,
    "anomaly_narration": PAYROLL_ROLES,
    "general": HR_ROLES | PAYROLL_ROLES,
}

TASK_TYPES = frozenset(_TASK_ROLES)


def _as_date(value: Optional[str]) -> Optional[date]:
    return date.fromisoformat(value) if value else None


async def _build(session, *, task_type: str, question: str, actor_email: str, params: dict):
    """Resolve the actor, apply §5, and assemble the task-specific context."""
    allowed = _TASK_ROLES.get(task_type)
    if allowed is None:
        raise HTTPException(
            400,
            f"'{task_type}' is not a supported AI task type. Supported: {sorted(TASK_TYPES)}.",
        )
    user = await actor_with_role(session, actor_email, allowed)

    if task_type == "payslip_explanation":
        payslip_id = params.get("payslip_id")
        if not payslip_id:
            raise HTTPException(400, "payslip_id is required to explain a payslip.")
        payslip = (
            await session.execute(select(Payslip).where(Payslip.public_id == payslip_id))
        ).scalars().first()
        if payslip is None:
            raise HTTPException(404, f"Payslip '{payslip_id}' not found")
        return await context_builder.build_payslip_explanation_context(
            session,
            payslip,
            question=question,
            include_department=bool(params.get("include_department")),
        )

    if task_type == "payroll_variance":
        return await context_builder.build_department_variance_context(
            session,
            question=question,
            department_public_id=params.get("department_id") or None,
            period_start=_as_date(params.get("period_start")),
            period_end=_as_date(params.get("period_end")),
        )

    if task_type == "pending_actions":
        return await context_builder.build_payroll_blockers_context(
            session, question=question, payrun_public_id=params.get("payrun_id") or None
        )

    if task_type == "anomaly_narration":
        return await context_builder.build_anomaly_context(
            session,
            question=question,
            period_start=_as_date(params.get("period_start")),
            period_end=_as_date(params.get("period_end")),
            department_public_id=params.get("department_id") or None,
        )

    # "general" — an employee-centred question joining several HR concepts.
    employee_id = params.get("employee_id")
    if not employee_id:
        raise HTTPException(
            400,
            "employee_id is required for a general HR question, so the answer is scoped to "
            "a specific person's authoritative records rather than to the whole database.",
        )
    employee = await EmployeeRepository(session).get_by_public_id(employee_id)
    if employee is None:
        raise HTTPException(404, f"Employee '{employee_id}' not found")
    from app.services.employee import EmployeeService

    EmployeeService(session).assert_can_read(user, employee)
    return await context_builder.build_employee_context(
        session,
        employee,
        question=question,
        period_start=_as_date(params.get("period_start")),
        period_end=_as_date(params.get("period_end")),
    )


async def assemble(*, task_type: str, question: str, actor_email: str, params: dict) -> dict:
    """The prompt and the facts behind it, ready for a provider call."""
    from app.core.telemetry import ai_span

    async with AsyncSessionLocal() as session:
        with ai_span("assemble_context", task_type=task_type) as span:
            context = await _build(
                session,
                task_type=task_type,
                question=question,
                actor_email=actor_email,
                params=params,
            )
            # Section names and counts. Not the facts themselves — those are
            # payroll, and a span is a telemetry boundary like any other.
            span.set_attribute("fact_sections", len(context.facts))
            span.set_attribute("unavailable_count", len(context.unavailable))
        # Read-only work. Rolled back explicitly so a lazy-load that opened a
        # transaction cannot leave one idle in the pool.
        await session.rollback()

    return {
        "prompt": prompts.render(context),
        "facts": context.facts,
        "unavailable": context.unavailable,
        "sources": context.sources,
        "subject": context.subject,
    }


# --------------------------------------------------------------------------
# Proposals
# --------------------------------------------------------------------------


async def _leave_proposal_context(session, params: dict, question: str, user):
    """The facts a human needs in order to judge a proposed leave request:
    who, what type, the balance it would draw down, and what else is already
    approved around those dates."""
    from app.ai.context_builder import AIContext, _approved_leave_in_period, _leave_balances
    from app.services.employee import EmployeeService

    employee = await EmployeeRepository(session).get_by_public_id(params.get("employee_id") or "")
    if employee is None:
        raise HTTPException(404, f"Employee '{params.get('employee_id')}' not found")
    EmployeeService(session).assert_can_read(user, employee)

    date_from = date.fromisoformat(params["date_from"])
    date_to = date.fromisoformat(params["date_to"])
    if date_to < date_from:
        raise HTTPException(422, "date_to must not precede date_from")

    context = AIContext(
        task_type="leave_proposal",
        question=question,
        subject={
            "action": "create_time_off_request",
            "employee_id": employee.public_id,
            "employee_name": employee.full_name,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
        },
    )
    context.add(
        "requested_action",
        {
            "employee_id": employee.public_id,
            "employee_name": employee.full_name,
            "time_off_type_id": params.get("time_off_type_id"),
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "reason": params.get("reason") or None,
        },
        source="the user's request, as parsed",
    )
    context.add("leave_balances", await _leave_balances(session, employee), source="TimeOffAllocation query")
    context.add(
        "existing_approved_leave_around_these_dates",
        await _approved_leave_in_period(session, employee, date_from, date_to),
        source="TimeOffRequest query (status=approved)",
    )
    return context, employee


async def propose(*, action: str, params: dict, question: str, actor_email: str) -> dict:
    """Investigate, ask the model for a rationale, and park a proposal.

    The model's role here is narrow and its failure modes are contained: it
    writes the rationale a human reads, and it may decline. It does not choose
    the parameters (they are what the caller asked for), it does not decide
    whether the action is permitted (the service does, at execution), and its
    "propose" is not an approval of anything — a proposal with a glowing
    rationale still executes nothing until a person confirms it.

    A provider outage therefore degrades gracefully rather than blocking the
    feature: the proposal is still created, carrying the facts and an explicit
    note that no narration was available. The human sees the same balance and
    the same dates either way.
    """
    from app.ai import proposals

    if action not in proposals.KNOWN_ACTIONS:
        raise HTTPException(
            400,
            f"'{action}' is not an action the AI layer may propose. "
            f"Proposable actions: {sorted(proposals.KNOWN_ACTIONS)}.",
        )

    async with AsyncSessionLocal() as session:
        user = await resolve_actor(session, actor_email)
        context, _employee = await _leave_proposal_context(session, params, question, user)

        decision = await AIDecisionNode.evaluate(
            workflow_context=context.as_payload(),
            prompt_template=prompts.decision_template(context),
            allowed_decisions=("propose", "decline"),
            fallback_decision="decline",
            task_type="leave_proposal",
        )

        rationale = decision.rationale
        if decision.status == "ai_unavailable":
            rationale = (
                "No AI provider was available to write a rationale. The proposal below is "
                "the request exactly as asked, with the authoritative balance and existing "
                "leave shown alongside it. Review those figures directly before confirming."
            )

        record = await proposals.create(
            session,
            action=action,
            params=params,
            proposed_for_email=actor_email,
            rationale=rationale,
            ai_status=decision.status,
            ai_provider=decision.provider,
            raw_output=decision.raw_output,
            context_facts=context.facts,
        )
        await session.commit()

    return {
        "status": "completed",
        "result": {
            "proposal": record,
            "ai_decision": decision.decision,
            "ai_status": decision.status,
            "facts": context.facts,
            "unavailable_information": context.unavailable,
            "fact_sources": context.sources,
            "requires_human_confirmation": True,
            "confirm_endpoint": f"/api/v1/ai/proposals/{record['proposal_id']}/confirm",
        },
    }


__all__ = ["TASK_TYPES", "assemble", "propose"]
