"""HR/Payroll prompt templates (Architecture §8.1).

One template per task type. Each renders four clearly separated blocks so the
model can never confuse a fact it was given with a number it might produce:

    AUTHORITATIVE ERP FACTS   what the deterministic system asserts
    UNAVAILABLE INFORMATION   what the system could NOT establish
    QUESTION                  what the human asked
    YOUR TASK                 what kind of answer is wanted

THE ONE INSTRUCTION EVERY TEMPLATE CARRIES
------------------------------------------
The model explains figures; it never produces them. If the ERP says NET is
52000.00, the answer says why the ERP produced 52000.00 — it does not add up
the lines and offer a second opinion, and it does not "correct" a figure it
finds surprising. A competing payroll calculation from a language model is the
single worst failure mode available to this feature: it would be fluent,
plausible, and wrong, and it would be quoted to an employee about their pay.

The second standing instruction is about absence. `UNAVAILABLE INFORMATION` is
not filler — it is the list of things the model is forbidden to guess. A model
that invents a cause for a pay change is more damaging than one that says the
data does not show why, because the invented cause is actionable and the honest
answer is merely disappointing.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.ai.context_builder import AIContext

#: Prepended to every template. Stated as rules rather than as a persona,
#: because a persona ("you are a helpful payroll assistant") does not constrain
#: behaviour and these constraints are the product.
_GROUND_RULES = """\
You are the explanation layer of PeoplePay360, an HR and payroll system.

Rules you must follow, in order of importance:

1. The ERP's figures are authoritative and final. Explain how they arose. Never
   recalculate a payslip, never add up lines to check a total, and never state
   a monetary amount that does not appear verbatim in the facts below.
2. Cite only facts given below. If the facts do not establish WHY something
   changed, say plainly that the available data does not show the cause. Never
   supply a plausible-sounding reason that the data does not support.
3. Anything listed under UNAVAILABLE INFORMATION is unknown. Say it is unknown.
   Do not estimate it, and do not reason as though it were zero.
4. Distinguish three things in your answer: facts the ERP asserts; changes
   visible between periods; and causal links those changes support. Do not
   present a correlation as a cause.
5. Amounts are exact decimal strings. Reproduce them exactly as given. Do not
   round, reformat, convert, or perform arithmetic on them.
6. Be concise and specific. A payroll manager is reading this to decide what to
   do next, not to be reassured.
"""

_PAYSLIP_EXPLANATION_TASK = """\
Explain this payslip, and in particular what is different about it compared to
the employee's previous period.

Work through the evidence in this order and say which link actually accounts
for the difference:
  previous payslip -> contract wage change -> attendance (worked days)
  -> unpaid leave / Loss of Pay -> salary rules applied -> this payslip

If several factors moved, say which one dominates and quote the figures that
show it. If the periods are not comparable, or the inputs behind one of them
are unavailable, say so instead of comparing anyway.
"""

_PAYROLL_VARIANCE_TASK = """\
Explain the movement in this department's payroll between the two periods
given.

Ground the explanation in the listed candidate causes: contract wage changes in
the window, the change in the number of payslips generated, and headcount in
scope. If the listed causes do not account for the movement, say that they do
not, and name what additional information would be needed. Do not attribute the
movement to a cause that is not in the facts.
"""

_PENDING_ACTIONS_TASK = """\
Say what is currently blocking payroll from being finalized, and what should be
done about it.

Blocking findings stop a payrun being validated; advisory ones do not. Lead
with the blocking ones, name the affected employee for each, and state the
concrete fix the finding itself specifies. Where a finding requires a
recomputation after the underlying record is fixed, say so — fixing the record
alone does not clear it.

If nothing is blocking, say that plainly rather than listing advisory noise as
though it were a blocker.
"""

_ANOMALY_NARRATION_TASK = """\
Narrate the deterministic anomaly signals given below.

These signals were produced by the application's own checks; treat every one of
them as real and none of them as suspect. Your job is to explain what each
means in practice, group related ones, and say which deserve attention first.

Do not invent additional anomalies, and do not describe a pattern that the
listed signals do not show. If the list is empty, say that no anomaly check
fired for this period.
"""

_GENERAL_TASK = """\
Answer the question using the facts below.

Join the HR concepts as needed — identity, contracts, attendance, leave and
payroll are all provided where relevant. Be explicit about which facts support
your answer, and about anything the question asks for that the data does not
contain.
"""

_PROPOSAL_TASK = """\
The user has asked you to perform an action. You may NOT perform it.

Produce a proposal for a human to approve or reject. State:
  - exactly what would be created or changed, with concrete values;
  - the facts below that make it valid or questionable (for leave: the balance,
    any overlapping request, whether the type affects payroll);
  - anything that looks wrong or missing, which the approver should see before
    they confirm.

If the facts show the action would be rejected by validation — for example an
insufficient leave balance — say so clearly in the rationale. Do not adjust the
request to make it fit; propose what was asked for and let the human decide.

Respond with JSON only, in exactly this shape:
{{"decision": "propose" or "decline", "rationale": "<two to four sentences>"}}
"""

_TASKS: dict[str, str] = {
    "payslip_explanation": _PAYSLIP_EXPLANATION_TASK,
    "payroll_variance": _PAYROLL_VARIANCE_TASK,
    "pending_actions": _PENDING_ACTIONS_TASK,
    "anomaly_narration": _ANOMALY_NARRATION_TASK,
    "general": _GENERAL_TASK,
    "leave_proposal": _PROPOSAL_TASK,
}

#: Task types this module knows how to frame. `app/ai/cache.py` tunes a TTL per
#: entry, and an unknown type still works — it just falls back to the general
#: framing and the default TTL.
KNOWN_TASK_TYPES = frozenset(_TASKS)


def task_instructions(task_type: str) -> str:
    return _TASKS.get(task_type, _GENERAL_TASK)


def render(context: "AIContext") -> str:
    """Build the full prompt for an assembled context.

    The facts are rendered as JSON rather than prose. Prose would force this
    function to decide what is worth saying — which is exactly the editorial
    judgement that belongs to the model, and exactly where a summariser would
    quietly drop the one fact that explained the change.
    """
    payload = context.as_payload()
    unavailable = payload["unavailable_information"] or [
        "Nothing relevant was unavailable for this question."
    ]
    return (
        f"{_GROUND_RULES}\n"
        "=== AUTHORITATIVE ERP FACTS (deterministic, already calculated) ===\n"
        f"{json.dumps(payload['authoritative_facts'], indent=2, sort_keys=False, default=str)}\n\n"
        "=== CONTEXT METADATA (what this is about, and where the facts came from) ===\n"
        f"{json.dumps({'subject': payload['subject'], 'fact_sources': payload['fact_sources']}, indent=2, default=str)}\n\n"
        "=== UNAVAILABLE INFORMATION (you may not guess at any of this) ===\n"
        f"{json.dumps(unavailable, indent=2)}\n\n"
        "=== QUESTION FROM THE USER ===\n"
        f"{context.question}\n\n"
        "=== YOUR TASK ===\n"
        f"{task_instructions(context.task_type)}"
    )


def decision_template(context: "AIContext") -> str:
    """A `str.format()` template for `AIDecisionNode.evaluate`.

    The node formats its own `{context}` slot with the JSON of the workflow
    context, so this returns everything EXCEPT the facts and leaves that one
    placeholder for it. The literal JSON braces in the proposal instructions are
    already doubled (`{{`/`}}`) at their definition, which is why this can be
    concatenated rather than escaped on the way past — escaping a whole prompt
    at call time is how a `{code}` in someone's leave reason turns into a
    KeyError in production.
    """
    return (
        f"{_GROUND_RULES}\n"
        "=== QUESTION FROM THE USER ===\n"
        f"{context.question}\n\n"
        "=== YOUR TASK ===\n"
        f"{task_instructions(context.task_type)}\n"
        "=== AUTHORITATIVE ERP FACTS (deterministic, already calculated) ===\n"
        "{context}"
    )


__all__ = ["KNOWN_TASK_TYPES", "decision_template", "render", "task_instructions"]
