"""
AI Decision Node — generic, reusable across all workflows.

Architecture ref: Section 5 — "An AI decision node proposes a transition with
a written rationale string."

evaluate() calls provider_router.generate() DIRECTLY (not via kiq()),
because it is always invoked from inside a Taskiq worker task. Double-hopping
through Taskiq risks a worker deadlock at concurrency=1.

This module has NO entity-specific knowledge: no fixed decision vocabulary, no
hardcoded prompt. Callers (see app/ai/review_job.py and its per-entity task
modules) supply their own prompt_template + allowed_decisions + fallback_decision.
"""
import json
import logging
from typing import Optional, Sequence
from pydantic import BaseModel
from app.ai.provider_router import generate, AIUnavailableError

logger = logging.getLogger("harmonix360.ai.decision_nodes")


class AIDecision(BaseModel):
    """Typed decision object returned by the AI decision node.

    `decision` is intentionally a plain str, not a fixed Literal — its allowed
    values are whatever the caller passed as `allowed_decisions` to evaluate().
    """
    decision: str
    status: str  # "ok", "ai_unavailable", "ai_parse_error"
    rationale: str
    raw_output: Optional[str] = None
    provider: str
    #: Set only when status == "ai_unavailable" — see
    #: `provider_router.AIUnavailableError.reason`. Lets a caller distinguish
    #: "try again shortly" from "nobody has configured a key" without parsing
    #: `rationale`'s prose.
    reason: Optional[str] = None


class AIDecisionNode:
    """
    Generic AI decision node for workflow transitions.
    Built once, reusable across any workflow — not per-entity.

    Always called from within a Taskiq worker task (never from a request handler).
    Calls generate() directly — no second Taskiq hop.
    """

    @staticmethod
    async def evaluate(
        workflow_context: dict,
        prompt_template: str,
        allowed_decisions: Sequence[str],
        fallback_decision: str,
        task_type: str = "generic_decision",
    ) -> AIDecision:
        """
        Build a structured prompt from workflow_context, call generate() directly,
        parse the response into an AIDecision.

        prompt_template: a str.format()-style template with a `{context}` slot —
            entity-specific (instructs the AI on the decision vocabulary + rules).
        allowed_decisions: the vocabulary this call accepts (e.g.
            ("approve", "escalate", "reject") for transfers, something entirely
            different for another entity type). A decision outside this set
            falls back to `fallback_decision`.
        fallback_decision: used when the AI is unavailable, its response can't be
            parsed, or it returns a decision outside allowed_decisions. Must
            itself be a member of allowed_decisions.
        """
        if fallback_decision not in allowed_decisions:
            raise ValueError(
                f"fallback_decision {fallback_decision!r} must be one of {list(allowed_decisions)!r}"
            )

        context_str = json.dumps(workflow_context, indent=2, default=str)
        prompt = prompt_template.format(context=context_str)

        try:
            # Direct call — NOT via run_ai_generate.kiq()
            ai_response = await generate(
                prompt=prompt,
                context=workflow_context,
                task_type=task_type,
            )
        except AIUnavailableError as e:
            logger.warning(
                "All AI providers unavailable for decision node (task_type=%s, reason=%s)",
                task_type, e.reason,
            )
            rationale = (
                "The AI assistant is temporarily unavailable — the request volume limit "
                "was reached. Forcing human review; try again shortly."
                if e.reason == "rate_limited"
                else "All AI providers are currently unreachable. Forcing human review."
            )
            return AIDecision(
                decision=fallback_decision,
                status="ai_unavailable",
                rationale=rationale,
                raw_output=None,
                provider="none",
                reason=e.reason,
            )

        # Parse the AI response
        try:
            raw_text = ai_response.text.strip()
            # Try to extract JSON from the response (handle markdown code blocks)
            if "```json" in raw_text:
                raw_text = raw_text.split("```json")[1].split("```")[0].strip()
            elif "```" in raw_text:
                raw_text = raw_text.split("```")[1].split("```")[0].strip()

            parsed = json.loads(raw_text)
            decision = str(parsed.get("decision", fallback_decision)).lower()
            rationale = parsed.get("rationale", "No rationale provided.")

            # Validate decision value against the caller's vocabulary
            if decision not in allowed_decisions:
                original = parsed.get("decision")
                decision = fallback_decision
                rationale = (
                    f"AI returned invalid decision '{original}'. "
                    f"Falling back to '{fallback_decision}' for human review."
                )

            return AIDecision(
                decision=decision,
                status="ok",
                rationale=rationale,
                raw_output=ai_response.text,
                provider=ai_response.provider,
            )
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            logger.warning("Failed to parse AI decision response: %s", e)
            return AIDecision(
                decision=fallback_decision,
                status="ai_parse_error",
                rationale=f"Could not parse AI response. Forcing human review. Parse error: {e}",
                raw_output=ai_response.text,
                provider=ai_response.provider,
            )
