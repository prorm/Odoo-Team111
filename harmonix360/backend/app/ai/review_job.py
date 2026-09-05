"""
Generic "AI decision node -> persist -> audit log" job.

Extracted from TransferService.process_ai_decision + jobs/tasks/transfer_decision.py
so a second entity type can get AI review without a hand-written parallel service
method and task — only a prompt, a status_map, and a thin task wrapper are needed.
See app/jobs/tasks/transfer_decision.py and app/jobs/tasks/booking_decision.py for
two different call sites (different vocabularies, different status_maps).
"""
import logging
from typing import Any, Dict, Optional
from app.services.base import BaseService
from app.ai.decision_nodes import AIDecisionNode, AIDecision

logger = logging.getLogger("harmonix360.ai.review_job")


class AIReviewJob:
    """
    Wires AIDecisionNode.evaluate() to a BaseService-backed entity: fetch by
    public_id -> (optional) validate current status -> call the AI -> apply
    status_map[decision] -> persist + audit-log via BaseService.update().

    entity_service:    a BaseService subclass instance (repo + entity_name already wired).
    status_map:        {decision_value: new_status_value}. Also defines the AI's
                        allowed decision vocabulary (status_map.keys()) — nothing
                        forces every decision to map to the same status; a decision
                        can drive genuinely different next-states per entity.
    prompt_template:   passed straight to AIDecisionNode.evaluate(); must contain "{context}".
    fallback_decision: decision key used when the AI is unavailable / unparseable /
                        returns a value outside status_map. Must be a key of status_map.
    status_field:      attribute name on the entity holding its lifecycle status.
    required_status:   if set, the entity must currently be in this status or run() raises.
    """

    def __init__(
        self,
        entity_service: BaseService,
        status_map: Dict[str, Any],
        prompt_template: str,
        fallback_decision: str,
        status_field: str = "status",
        required_status: Optional[Any] = None,
        decision_data_field: Optional[str] = "ai_decision_data",
        note_field: Optional[str] = "decision_note",
    ):
        if fallback_decision not in status_map:
            raise ValueError(f"fallback_decision {fallback_decision!r} must be a key of status_map")
        self.entity_service = entity_service
        self.status_map = status_map
        self.prompt_template = prompt_template
        self.fallback_decision = fallback_decision
        self.status_field = status_field
        self.required_status = required_status
        self.decision_data_field = decision_data_field
        self.note_field = note_field

    async def run(self, public_id: str, workflow_context: dict, task_type: str) -> Any:
        entity = await self.entity_service.get_or_404(public_id)

        old_status = getattr(entity, self.status_field)
        if self.required_status is not None and old_status != self.required_status:
            raise ValueError(
                f"{self.entity_service.entity_name} '{public_id}' is not in required status "
                f"'{self.required_status}' (current: '{old_status}')"
            )

        decision: AIDecision = await AIDecisionNode.evaluate(
            workflow_context=workflow_context,
            prompt_template=self.prompt_template,
            allowed_decisions=list(self.status_map.keys()),
            fallback_decision=self.fallback_decision,
            task_type=task_type,
        )

        new_status = self.status_map[decision.decision]
        setattr(entity, self.status_field, new_status)
        if self.note_field:
            setattr(entity, self.note_field, decision.rationale)
        if self.decision_data_field:
            setattr(entity, self.decision_data_field, decision.model_dump())

        old_label = getattr(old_status, "value", old_status)
        new_label = getattr(new_status, "value", new_status)

        action = {
            "ai_unavailable": "AI_DECISION_UNAVAILABLE",
            "ai_parse_error": "AI_DECISION_PARSE_ERROR",
        }.get(decision.status, "AI_DECISION_PROPOSE")

        logger.info(
            "AI review for %s '%s': decision=%s status=%s provider=%s -> %s=%s",
            self.entity_service.entity_name, public_id, decision.decision,
            decision.status, decision.provider, self.status_field, new_label,
        )

        return await self.entity_service.update(
            entity,
            actor=f"ai:{decision.provider}",
            action=action,
            before_diff={self.status_field: old_label},
            after_diff={
                self.status_field: new_label,
                "decision": decision.decision,
                "rationale": decision.rationale,
                "provider": decision.provider,
                "ai_status": decision.status,
            },
            reason=decision.rationale,
        )
