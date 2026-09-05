"""Phase 9 AI-native demo, driven over real HTTP against a running stack.

    python -m scripts.ai_demo [--base-url http://localhost:8000]

Three demos, in the order the phase brief asks for them:

  DEMO 1  A contextual payroll question. Not "summarise this payslip" — the
          answer has to reach the previous period, the contract, attendance and
          unpaid leave to be right at all. The demo prints the investigation
          chain the context layer assembled, so what the model was told is
          visible next to what it said.
  DEMO 2  An anomaly question, answered from deterministic signals.
  DEMO 3  An AI-initiated mutation: investigate -> propose -> HUMAN CONFIRMS ->
          existing service -> validation -> database -> audit. The script
          asserts that nothing was written before the confirmation, and that
          the audit trail names the proposer, the confirmer and the outcome.

Everything goes through the public API with real logins. No direct database
writes, and no seeded shortcut: the August payrun this demo explains is created
and computed here, by the same endpoints B5/B6 expose.

WHEN NO AI PROVIDER IS CONFIGURED
---------------------------------
`GROQ_API_KEY` is empty in a fresh checkout, and Groq is the only provider —
there is no fallback. The demo still runs end to end and reports
`ai_unavailable` at the narration step, printing the authoritative facts that
would have been narrated. That is the designed
behaviour, not a broken demo: the deterministic half is the product, and the
model is a presentation layer over it. The script says so explicitly rather than
quietly printing an empty answer.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import date

import httpx

BASE = "/api/v1"

ADMIN = ("admin@peoplepay360.com", "admin123")
PAYROLL_MANAGER = ("payroll.manager@peoplepay360.com", "payroll123")
EMPLOYEE = ("employee@peoplepay360.com", "employee123")

#: The seeded Loss-of-Pay scenario: 3 approved unpaid days in August 2026
#: against a July baseline with none. That contrast is what makes DEMO 1 a real
#: question — the same employee, the same contract, a different net.
AUGUST = (date(2026, 8, 1), date(2026, 8, 31))
JULY_RUN_NAME = "July 2026 payroll (demo)"

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        _FAILURES.append(label)
    return condition


def heading(text: str) -> None:
    print(f"\n{'=' * 78}\n{text}\n{'=' * 78}")


async def login(client: httpx.AsyncClient, credentials: tuple[str, str]) -> dict:
    email, password = credentials
    response = await client.post(
        f"{BASE}/auth/login", json={"email": email, "password": password}
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def idem() -> dict:
    return {"Idempotency-Key": f"aidemo-{uuid.uuid4().hex}"}


async def poll_job(client: httpx.AsyncClient, headers: dict, job_id: str, *, tries: int = 40) -> dict:
    """Poll an AI job to a terminal state.

    A job that never leaves `pending` means the Taskiq worker is not running —
    reported as such rather than as an AI failure, because they need different
    fixes and the distinction is invisible from the response alone.
    """
    for _ in range(tries):
        response = await client.get(f"{BASE}/ai/jobs/{job_id}", headers=headers)
        response.raise_for_status()
        payload = response.json()
        if payload["status"] != "pending":
            return payload
        await asyncio.sleep(0.5)
    return {"status": "worker_not_running", "result": None, "error": (
        "The job stayed queued. Start the Taskiq worker: "
        "taskiq worker app.jobs.broker:broker app.jobs.tasks.ai_jobs app.jobs.tasks.payroll_jobs"
    )}


def show_answer(payload: dict) -> None:
    status = payload.get("status")
    result = payload.get("result") or {}
    if status == "completed":
        print("\n  --- AI ANSWER " + "-" * 60)
        for line in (result.get("answer") or "").splitlines():
            print(f"  {line}")
        print(f"  [provider={result.get('provider')} model={result.get('model')} "
              f"cached={result.get('cached')}]")
    elif status == "ai_unavailable":
        print("\n  --- AI UNAVAILABLE (clean state, facts intact) " + "-" * 26)
        print(f"  {payload.get('error')}")
    else:
        print(f"\n  --- job status: {status} — {payload.get('error')}")


def show_facts(result: dict, sections: tuple[str, ...]) -> None:
    facts = result.get("facts") or {}
    print("\n  --- AUTHORITATIVE FACTS THE ANSWER WAS BUILT FROM " + "-" * 23)
    for name in sections:
        if name in facts:
            rendered = json.dumps(facts[name], indent=2)
            if len(rendered) > 1400:
                rendered = rendered[:1400] + "\n  ... (truncated for the demo output)"
            print(f"\n  [{name}]")
            for line in rendered.splitlines():
                print(f"  {line}")
    unavailable = result.get("unavailable_information") or []
    if unavailable:
        print("\n  [unavailable_information — the model is forbidden to guess these]")
        for note in unavailable:
            print(f"  - {note}")
    print("\n  [fact_sources]")
    for source in result.get("fact_sources") or []:
        print(f"  - {source}")


# ---------------------------------------------------------------------------
# Setup: an August payrun for the Loss-of-Pay employee
# ---------------------------------------------------------------------------


async def find_lop_employee(client: httpx.AsyncClient, headers: dict) -> dict:
    """The seeded employee with approved unpaid leave in August 2026."""
    response = await client.get(
        f"{BASE}/employees/", params={"limit": 100}, headers=headers
    )
    response.raise_for_status()
    for employee in response.json()["items"]:
        if employee["work_email"] == "lop.demo@peoplepay360.com":
            return employee
    raise SystemExit(
        "The Loss-of-Pay demo employee is missing. Run `python -m app.seed` first."
    )


async def august_payslip(client: httpx.AsyncClient, headers: dict, employee: dict) -> dict:
    """Create and compute an August payrun for the LOP employee, through B5/B6.

    Compute is pressed HERE, by a payroll user's credentials — never by the AI
    layer, which has no tool that can do it.
    """
    structures = await client.get(
        f"{BASE}/salary-structures/", params={"limit": 50}, headers=headers
    )
    structures.raise_for_status()
    structure = next(
        s for s in structures.json()["items"] if s["code"] == "PP360_DEMO"
    )

    created = await client.post(
        f"{BASE}/payruns/",
        json={
            "name": f"AI demo August 2026 ({uuid.uuid4().hex[:6]})",
            "salary_structure_id": structure["id"],
            "period_start": AUGUST[0].isoformat(),
            "period_end": AUGUST[1].isoformat(),
            "employee_ids": [employee["id"]],
        },
        headers={**headers, **idem()},
    )
    created.raise_for_status()
    payrun = created.json()

    computed = await client.post(
        f"{BASE}/payruns/{payrun['id']}/compute",
        json={"version": payrun["version"]},
        headers={**headers, **idem()},
    )
    computed.raise_for_status()
    check(
        "August payrun computed through the real B6 endpoint",
        computed.json()["computed_count"] == 1,
        f"computed_count={computed.json()['computed_count']}",
    )

    payslips = await client.get(f"{BASE}/payruns/{payrun['id']}/payslips", headers=headers)
    payslips.raise_for_status()
    return {"payrun": payrun, "payslip": payslips.json()["items"][0]}


# ---------------------------------------------------------------------------
# DEMO 1
# ---------------------------------------------------------------------------


async def demo_one(client: httpx.AsyncClient, payroll: dict, employee: dict, payslip: dict) -> None:
    heading("DEMO 1 — CONTEXTUAL PAYROLL QUESTION")
    print(f'  Asking: "Why did {employee["first_name"]}\'s salary change this month?"')
    print(f"  About payslip {payslip['id']} (August 2026), net {payslip['net_amount']}")

    response = await client.post(
        f"{BASE}/ai/ask",
        json={
            "question": (
                f"Why did {employee['first_name']} {employee['last_name']}'s salary "
                "change this month compared to last month?"
            ),
            "task_type": "payslip_explanation",
            "params": {"payslip_id": payslip["id"]},
        },
        headers=payroll,
    )
    check("POST /ai/ask returned 202 (enqueued, not called inline)", response.status_code == 202,
          f"status={response.status_code}")
    payload = await poll_job(client, payroll, response.json()["job_id"])
    result = payload.get("result") or {}

    comparison = (result.get("facts") or {}).get("comparison_with_previous_period") or {}
    check(
        "The context reached BEYOND the current payslip to the previous period",
        bool(comparison.get("comparable")),
        f"previous payslip = {(comparison.get('previous') or {}).get('payslip_id')}",
    )
    if comparison.get("comparable"):
        net = comparison["totals"]["net_amount"]
        check(
            "A real net change was detected from persisted payslips",
            net["direction"] != "unchanged",
            f"{net['before']} -> {net['after']} ({net['delta']})",
        )
        inputs = comparison.get("input_changes") or {}
        check(
            "The cause is traceable to a changed computation input",
            any(v.get("direction") == "increase" for v in inputs.values()),
            ", ".join(
                f"{k}: {v.get('before')} -> {v.get('after')}"
                for k, v in inputs.items()
                if v.get("direction") not in (None, "unchanged")
            ),
        )
    facts = result.get("facts") or {}
    check(
        "Contract history was investigated (wage-change link in the chain)",
        "contract_history" in facts,
        f"contracts={facts.get('contract_history', {}).get('contract_count')}, "
        f"wage_changes={len(facts.get('contract_history', {}).get('wage_changes', []))}",
    )
    check("Attendance was investigated", "attendance" in facts,
          f"worked_days={facts.get('attendance', {}).get('worked_days')}")
    check("Time off was investigated", "time_off" in facts,
          f"unpaid_leave_days={facts.get('time_off', {}).get('unpaid_leave_days')}")

    show_facts(result, ("comparison_with_previous_period", "attendance", "time_off"))
    show_answer(payload)


# ---------------------------------------------------------------------------
# DEMO 2
# ---------------------------------------------------------------------------


async def demo_two(client: httpx.AsyncClient, payroll: dict) -> None:
    heading("DEMO 2 — ANOMALY / PATTERN QUESTION")
    print('  Asking: "Are there any unusual payroll or HR patterns I should know about?"')

    response = await client.post(
        f"{BASE}/ai/ask",
        json={
            "question": "Are there any unusual payroll or HR patterns I should know about?",
            "task_type": "anomaly_narration",
            "params": {
                "period_start": AUGUST[0].isoformat(),
                "period_end": AUGUST[1].isoformat(),
            },
        },
        headers=payroll,
    )
    check("POST /ai/ask (anomaly_narration) returned 202", response.status_code == 202)
    payload = await poll_job(client, payroll, response.json()["job_id"])
    result = payload.get("result") or {}
    facts = result.get("facts") or {}

    check(
        "Signals came from deterministic application queries, not the model",
        any("deterministic" in s for s in result.get("fact_sources") or []),
        "; ".join(result.get("fact_sources") or []),
    )
    summary = facts.get("signal_summary") or {}
    check(
        "The signal count and the signal list agree",
        summary.get("total") == len(facts.get("deterministic_signals") or []),
        f"total={summary.get('total')} by_type={summary.get('by_type')}",
    )
    show_facts(result, ("signal_summary", "deterministic_signals"))
    show_answer(payload)


# ---------------------------------------------------------------------------
# DEMO 3
# ---------------------------------------------------------------------------


async def demo_three(client: httpx.AsyncClient, admin: dict) -> None:
    heading("DEMO 3 — AI ACTION: PROPOSE -> HUMAN CONFIRM -> EXECUTE")

    employee_headers = await login(client, EMPLOYEE)
    me = await client.get(f"{BASE}/auth/me", headers=employee_headers)
    me.raise_for_status()
    employee_id = me.json().get("employee_id")
    if not employee_id:
        print("  SKIPPED: the seeded employee login is not linked to an Employee row.")
        return

    types = await client.get(
        f"{BASE}/time-off-types/", params={"limit": 100}, headers=admin
    )
    types.raise_for_status()
    leave_type = next(
        (t for t in types.json()["items"] if not t["requires_allocation"]),
        types.json()["items"][0],
    )

    async def request_count() -> int:
        response = await client.get(
            f"{BASE}/time-off-requests/", params={"employee_id": employee_id, "limit": 100},
            headers=admin,
        )
        response.raise_for_status()
        return response.json()["total"]

    before = await request_count()
    target = date(2026, 10, 2)
    print(f'  Asking: "Request one day of leave for me on {target}."')

    proposal_response = await client.post(
        f"{BASE}/ai/proposals",
        json={
            "action": "create_time_off_request",
            "question": f"Request one day of leave for me on {target}.",
            "params": {
                "employee_id": employee_id,
                "time_off_type_id": leave_type["id"],
                "date_from": target.isoformat(),
                "date_to": target.isoformat(),
                "reason": "Requested through the AI assistant",
            },
        },
        headers=employee_headers,
    )
    check("POST /ai/proposals returned 202", proposal_response.status_code == 202,
          f"status={proposal_response.status_code} {proposal_response.text[:200]}")
    payload = await poll_job(client, employee_headers, proposal_response.json()["job_id"])
    if payload["status"] != "completed":
        check("Proposal job completed", False, f"{payload['status']}: {payload.get('error')}")
        return

    result = payload["result"]
    proposal = result["proposal"]
    print(f"\n  PROPOSED: {proposal['summary']}")
    print(f"  RATIONALE: {proposal['rationale']}")
    print(f"  ai_status={proposal['ai_status']} provider={proposal['ai_provider']}")

    check("The proposal requires human confirmation", result["requires_human_confirmation"] is True)
    check("Proposal state is PENDING_REVIEW", proposal["status"] == "pending_review")
    check(
        "NOTHING was written to the domain by the proposal",
        await request_count() == before,
        f"time-off requests still {before}",
    )

    readback = await client.get(
        f"{BASE}/ai/proposals/{proposal['proposal_id']}", headers=employee_headers
    )
    check("The human can read the pending proposal before deciding", readback.status_code == 200)

    print("\n  >>> HUMAN CONFIRMS <<<")
    confirm = await client.post(
        f"{BASE}/ai/proposals/{proposal['proposal_id']}/confirm", headers=employee_headers
    )
    check("Confirmation executed through the existing service", confirm.status_code == 200,
          confirm.text[:200])
    if confirm.status_code == 200:
        created = confirm.json()["result"]
        print(f"  CREATED: request {created['request_id']} "
              f"{created['date_from']}..{created['date_to']} status={created['status']}")
        check("Exactly one record was created", await request_count() == before + 1)
        check(
            "The created record went through the normal approval workflow",
            created["status"] in ("to_approve", "approved"),
            f"status={created['status']}",
        )

    replay = await client.post(
        f"{BASE}/ai/proposals/{proposal['proposal_id']}/confirm", headers=employee_headers
    )
    check("A confirmed proposal cannot be replayed", replay.status_code == 404,
          f"status={replay.status_code}")

    # The audit trail has no REST read endpoint (it is exposed through the MCP
    # `query_audit_trail` tool, which needs the separate MCP process). This is a
    # read-only verification query, not a manual database intervention: every
    # mutation in this demo went through the API above, and nothing here writes.
    actions = await _audit_actions(proposal["proposal_id"])
    if actions is None:
        print("  [note] audit verification skipped: this script is not running with "
              "database access. tests/test_ai_mcp.py asserts the same rows.")
    else:
        check(
            "The audit trail records propose, confirm and execute",
            {"AI_PROPOSED_ACTION", "AI_CONFIRMED_ACTION", "AI_ACTION_EXECUTED"}
            <= {action for action, _ in actions},
            ", ".join(f"{action} by {actor}" for action, actor in sorted(set(actions))),
        )


async def _audit_actions(proposal_id: str):
    """Read-only: the audit rows written for one proposal, or None if this
    process cannot reach the database."""
    try:
        from sqlalchemy import select

        from app.core.database import AsyncSessionLocal
        from app.models.entities import AuditLog

        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(AuditLog.action, AuditLog.actor).where(
                        AuditLog.entity_id == proposal_id
                    )
                )
            ).all()
            await session.rollback()
        return [tuple(row) for row in rows]
    except Exception:
        return None


# ---------------------------------------------------------------------------


async def main(base_url: str) -> int:
    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
        admin = await login(client, ADMIN)
        payroll = await login(client, PAYROLL_MANAGER)

        heading("SETUP — an August 2026 payrun for the Loss-of-Pay employee")
        employee = await find_lop_employee(client, admin)
        print(f"  Employee: {employee['first_name']} {employee['last_name']} ({employee['id']})")
        built = await august_payslip(client, payroll, employee)
        payslip = built["payslip"]
        print(f"  August payslip {payslip['id']}: gross {payslip['gross_amount']}, "
              f"net {payslip['net_amount']}")

        await demo_one(client, payroll, employee, payslip)
        await demo_two(client, payroll)
        await demo_three(client, admin)

    heading("RESULT")
    if _FAILURES:
        print(f"  {len(_FAILURES)} check(s) FAILED:")
        for failure in _FAILURES:
            print(f"   - {failure}")
        return 1
    print("  All demo checks passed.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.base_url)))
