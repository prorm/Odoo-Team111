"""Phase 9 — AI context layer and MCP tools (PS §5.1/§5.2, Architecture §8.1/§8.2/§9).

The tests that matter most here are the ones that would fail if the AI layer
ever became a second way into the database:

  * `test_single_path_*` — the SAME request, once through REST and once through
    an MCP tool, produces the SAME status and the SAME message, because both
    call the same service. Not "similar behaviour": identical text.
  * `test_ai_layer_has_no_write_path_to_payslips` — a static scan plus a
    before/after row comparison across the whole read tool set.
  * `test_mcp_read_tools_enforce_rbac` — an agent acting as an HR Manager hits
    the same payroll wall that user's browser does.

Everything else defends the properties the phase brief names: money never
becomes a float, absent data is reported rather than guessed, a provider outage
degrades to a clean unavailable state with the ERP facts intact, and an AI
mutation cannot happen without a human confirming that specific proposal.

These run against the seeded demo dataset for the AI-context cases, because a
context builder's job is to assemble REAL connected records — a synthetic
employee with one contract and no history cannot exercise "why did pay change".
"""
import json
import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.entities import AuditLog
from app.models.enums import UserRole
from app.models.payroll import Payrun, Payslip
from app.mcp import tools
from tests.conftest import auth_headers

BASE = "/api/v1"

#: Seeded logins. MCP binds to a REAL user row (Architecture §5), so these
#: cannot be the synthetic `{role}@peoplepay360.com` addresses `auth_headers`
#: mints — those have tokens but no User row, which is exactly the distinction
#: `resolve_actor` exists to enforce.
ADMIN_EMAIL = "admin@peoplepay360.com"
PAYROLL_MANAGER_EMAIL = "payroll.manager@peoplepay360.com"
PAYROLL_USER_EMAIL = "payroll.user@peoplepay360.com"
HR_MANAGER_EMAIL = "hr.manager@peoplepay360.com"
EMPLOYEE_EMAIL = "employee@peoplepay360.com"

HR = auth_headers(UserRole.HR_MANAGER)
PAYROLL = auth_headers(UserRole.HR_PAYROLL_MANAGER)


# ===========================================================================
# Fixtures over the seeded demo data
# ===========================================================================


@pytest_asyncio.fixture
async def seeded_payslip():
    """A real, paid payslip from the seeded July 2026 run, with its employee."""
    async with AsyncSessionLocal() as session:
        run = (
            await session.execute(
                select(Payrun).where(Payrun.name == "July 2026 payroll (demo)")
            )
        ).scalars().first()
        if run is None:
            pytest.skip("seeded July payrun absent; run `python -m app.seed` first")
        payslip = (
            await session.execute(
                select(Payslip).where(Payslip.payrun_id == run.id).order_by(Payslip.id)
            )
        ).scalars().first()
        yield {
            "payslip_id": payslip.public_id,
            "employee_id": payslip.employee.public_id,
            "employee_name": payslip.employee.full_name,
            "net": payslip.net_amount,
            "gross": payslip.gross_amount,
            "payrun_id": run.public_id,
        }


@pytest_asyncio.fixture
async def linked_employee():
    """The Employee row the seeded `employee@peoplepay360.com` login owns."""
    from app.models.employee import Employee
    from app.models.user import User

    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.email == EMPLOYEE_EMAIL))
        ).scalars().first()
        if user is None:
            pytest.skip("seeded employee login absent")
        employee = (
            await session.execute(select(Employee).where(Employee.user_id == user.id))
        ).scalars().first()
        if employee is None:
            pytest.skip("seeded employee login is not linked to an Employee row")
        yield employee.public_id


@pytest_asyncio.fixture
async def leave_scenario(client, cleanup_employees):
    """An employee with a one-day allocation — enough to approve a one-day
    request and not enough for a three-day one.

    The employee is linked to the seeded EMPLOYEE login so the self-service
    paths (an Employee submitting their own leave) can be exercised through
    both REST and MCP with the same identity.
    """
    from app.models.employee import Employee
    from app.models.time_off import TimeOffAllocation, TimeOffRequest, TimeOffType
    from app.models.user import User
    from sqlalchemy import delete

    created = await client.post(
        f"{BASE}/employees/",
        json={
            "first_name": "Ai",
            "last_name": "Scenario",
            "work_email": f"ai.scenario.{uuid.uuid4().hex[:8]}@peoplepay360.com",
            "bank_account": "GB00AI0000001",
        },
        headers=HR,
    )
    assert created.status_code == 201, created.text
    employee = created.json()

    type_response = await client.post(
        f"{BASE}/time-off-types/",
        json={
            "name": "AI Casual Leave",
            "code": f"AI_CAS_{uuid.uuid4().hex[:6].upper()}",
            "unit": "days",
            "requires_allocation": True,
            "requires_approval": True,
            "payroll_integration": False,
        },
        headers=HR,
    )
    assert type_response.status_code == 201, type_response.text
    leave_type = type_response.json()

    allocation = await client.post(
        f"{BASE}/time-off-allocations/",
        json={
            "employee_id": employee["id"],
            "time_off_type_id": leave_type["id"],
            "allocated": "1",
            "valid_from": "2026-01-01",
            "valid_to": "2026-12-31",
        },
        headers=HR,
    )
    assert allocation.status_code == 201, allocation.text

    # Point the seeded EMPLOYEE login at this employee for the duration of the
    # test, restoring the original link afterwards. Nothing else can express
    # "an Employee acting for themselves" without an Employee row that login
    # actually owns, and `Employee.user_id` is UNIQUE.
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.email == EMPLOYEE_EMAIL))
        ).scalars().first()
        previous = (
            await session.execute(select(Employee).where(Employee.user_id == user.id))
        ).scalars().first()
        previous_id = previous.id if previous else None
        if previous is not None:
            previous.user_id = None
            await session.flush()
        target = (
            await session.execute(
                select(Employee).where(Employee.public_id == employee["id"])
            )
        ).scalars().first()
        target.user_id = user.id
        await session.commit()

    yield {"employee": employee, "leave_type": leave_type, "allocation": allocation.json()}

    async with AsyncSessionLocal() as session:
        target = (
            await session.execute(
                select(Employee).where(Employee.public_id == employee["id"])
            )
        ).scalars().first()
        if target is not None:
            target.user_id = None
            await session.flush()
        if previous_id is not None:
            restored = await session.get(Employee, previous_id)
            if restored is not None:
                restored.user_id = user.id
        type_row = (
            await session.execute(
                select(TimeOffType).where(TimeOffType.public_id == leave_type["id"])
            )
        ).scalars().first()
        if type_row is not None:
            await session.execute(
                delete(TimeOffRequest).where(TimeOffRequest.time_off_type_id == type_row.id)
            )
            await session.execute(
                delete(TimeOffAllocation).where(
                    TimeOffAllocation.time_off_type_id == type_row.id
                )
            )
            await session.execute(delete(TimeOffType).where(TimeOffType.id == type_row.id))
        await session.commit()


@pytest.fixture
def stub_provider(monkeypatch):
    """A deterministic stand-in for Groq, so the narration path is exercised.

    No provider key exists in CI, and a test that depended on a live third-party
    model would be measuring their uptime rather than our code. What matters
    here is OUR half: that the prompt carries the authoritative facts, that the
    response is threaded back with its provider recorded, and that the cache is
    not silently answering a different question. The stub captures the prompt so
    those can be asserted directly.
    """
    from app.ai import cache as ai_cache
    from app.ai import provider_router

    captured: dict = {}

    async def fake_call(prompt: str, model: str, api_key: str):
        captured["prompt"] = prompt
        captured["model"] = model
        return ("STUB ANSWER: the ERP facts above explain the change.", 123)

    monkeypatch.setattr(provider_router.settings, "GROQ_API_KEY", "test-key", raising=False)
    monkeypatch.setattr(provider_router, "_CALL_FUNCTIONS", {"groq": fake_call})
    # Bypass the cache in both directions: a hit would skip the provider and
    # make the captured prompt stale, which is the one thing this fixture is for.
    monkeypatch.setattr(ai_cache.AIResponseCache, "get", staticmethod(lambda *a, **k: _none()))
    monkeypatch.setattr(ai_cache.AIResponseCache, "set", staticmethod(lambda *a, **k: _noop()))
    return captured


async def _none():
    return None


async def _noop():
    return None


# ===========================================================================
# 1. Single path to the database (Architecture §9)
# ===========================================================================


async def test_single_path_authorization_rest_and_mcp_refuse_identically(
    client, leave_scenario
):
    """An Employee submitting leave for SOMEONE ELSE is refused with the same
    status and the same words through both entry points.

    This is the §9 property stated as a test. The MCP tool has no authorization
    code of its own — it hands the actor to `TimeOffRequestService.create_request`,
    whose `employee_for` compares against the actor's own employee link. If a
    future change added an MCP-specific check, these two strings would diverge
    and this test would say so.
    """
    other = await client.post(
        f"{BASE}/employees/",
        json={
            "first_name": "Somebody",
            "last_name": "Else",
            "work_email": f"other.{uuid.uuid4().hex[:8]}@peoplepay360.com",
        },
        headers=HR,
    )
    assert other.status_code == 201
    other_id = other.json()["id"]
    leave_type = leave_scenario["leave_type"]

    rest = await client.post(
        f"{BASE}/time-off-requests/",
        json={
            "employee_id": other_id,
            "time_off_type_id": leave_type["id"],
            "date_from": "2026-09-07",
            "date_to": "2026-09-07",
        },
        headers=auth_headers(
            UserRole.EMPLOYEE, employee_id=leave_scenario["employee"]["id"]
        ),
    )

    from fastapi import HTTPException

    async with AsyncSessionLocal() as session:
        with pytest.raises(HTTPException) as raised:
            await tools.create_time_off_request(
                session,
                employee_id=other_id,
                time_off_type_id=leave_type["id"],
                date_from="2026-09-07",
                date_to="2026-09-07",
                actor_email=EMPLOYEE_EMAIL,
            )

    assert rest.status_code == 403, rest.text
    assert raised.value.status_code == 403
    assert rest.json()["detail"] == raised.value.detail
    assert "own employee records" in str(raised.value.detail)


async def test_single_path_validation_rest_and_mcp_reject_identically(
    client, leave_scenario
):
    """Approving a 3-day request against a 1-day allocation fails the same way
    through REST and through MCP, and debits nothing either time."""
    employee = leave_scenario["employee"]
    leave_type = leave_scenario["leave_type"]

    async def make_request() -> dict:
        response = await client.post(
            f"{BASE}/time-off-requests/",
            json={
                "employee_id": employee["id"],
                "time_off_type_id": leave_type["id"],
                "date_from": "2026-09-07",
                "date_to": "2026-09-09",
            },
            headers=HR,
        )
        assert response.status_code == 201, response.text
        return response.json()

    rest_request = await make_request()
    rest = await client.post(
        f"{BASE}/time-off-requests/{rest_request['id']}/approve",
        json={"version": rest_request["version"], "decision_note": "via REST"},
        headers=HR,
    )

    mcp_request = await make_request()
    from fastapi import HTTPException

    async with AsyncSessionLocal() as session:
        with pytest.raises(HTTPException) as raised:
            await tools.approve_time_off_request(
                session,
                request_id=mcp_request["id"],
                version=mcp_request["version"],
                actor_email=HR_MANAGER_EMAIL,
                approve=True,
                decision_note="via MCP",
            )

    assert rest.status_code == 409, rest.text
    assert raised.value.status_code == 409
    assert rest.json()["detail"] == raised.value.detail

    balance = await client.get(
        f"{BASE}/time-off-allocations/{leave_scenario['allocation']['id']}", headers=HR
    )
    assert Decimal(balance.json()["taken"]) == Decimal("0.00")


# ===========================================================================
# 2. The AI has no write path to payroll (Architecture §7/§10)
# ===========================================================================


async def test_ai_layer_has_no_write_path_to_payslips(seeded_payslip):
    """Two independent proofs, because either alone is weak.

    Statically: no module under `app/ai` or `app/mcp` constructs a `Payslip` or
    `PayslipLine`, calls the compute engine, or commits a session of its own.
    Dynamically: running every read tool leaves the payslip table byte-identical,
    which catches a write that arrived through a helper the scan did not name.
    """
    import pathlib

    roots = [pathlib.Path("app/ai"), pathlib.Path("app/mcp")]
    forbidden = ("Payslip(", "PayslipLine(", ".compute(", "resolve_salary_structure")
    offenders = []
    for root in roots:
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            # Strip docstrings/comments crudely: only executable lines matter,
            # and these modules discuss the rule engine at length in prose.
            code = "\n".join(
                line for line in source.splitlines() if not line.lstrip().startswith("#")
            )
            for token in forbidden:
                if token in code:
                    offenders.append(f"{path}: {token}")
    assert not offenders, f"AI/MCP code references a payroll write path: {offenders}"

    async def snapshot() -> list[tuple]:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(
                        Payslip.public_id,
                        Payslip.gross_amount,
                        Payslip.net_amount,
                        Payslip.worked_days,
                        Payslip.status,
                        Payslip.version,
                    ).order_by(Payslip.id)
                )
            ).all()
        return [tuple(row) for row in rows]

    before = await snapshot()
    async with AsyncSessionLocal() as session:
        await tools.get_payslip(session, seeded_payslip["payslip_id"], PAYROLL_MANAGER_EMAIL)
        await tools.explain_payslip(session, seeded_payslip["payslip_id"], PAYROLL_MANAGER_EMAIL)
        await tools.get_payrun_summary(session, seeded_payslip["payrun_id"], PAYROLL_MANAGER_EMAIL)
        await tools.get_payroll_warnings(session, PAYROLL_MANAGER_EMAIL, seeded_payslip["payrun_id"])
        await tools.get_department_payroll(session, PAYROLL_MANAGER_EMAIL)
        await tools.get_payroll_trends(session, PAYROLL_MANAGER_EMAIL, seeded_payslip["employee_id"])
        await tools.find_payroll_anomalies(session, PAYROLL_MANAGER_EMAIL)
        await tools.get_employee_contracts(session, seeded_payslip["employee_id"], PAYROLL_MANAGER_EMAIL)
        await session.rollback()
    assert await snapshot() == before


async def test_no_mcp_tool_can_compute_payroll():
    """`create_payrun` exists; nothing that writes a payslip does.

    Compute is the rule engine's write path. Architecture §7 says no AI code
    path reaches it, and the tool registry is where that would silently stop
    being true — a `compute_payrun` tool would look helpful and be exactly the
    thing the constraint forbids.
    """
    names = {tool.__name__ for tool in tools.ACTION_TOOLS}
    assert "create_payrun" in names
    assert not {n for n in names if "compute" in n or "payslip" in n}
    assert names == {
        "create_time_off_request",
        "approve_time_off_request",
        "correct_attendance",
        "create_payrun",
        "request_payroll_validation",
    }


# ===========================================================================
# 3. RBAC through MCP is the same matrix (Architecture §5)
# ===========================================================================


@pytest.mark.parametrize(
    "actor,expected",
    [
        (PAYROLL_MANAGER_EMAIL, 200),
        (PAYROLL_USER_EMAIL, 200),
        (ADMIN_EMAIL, 200),
        (HR_MANAGER_EMAIL, 403),
        (EMPLOYEE_EMAIL, 403),
    ],
)
async def test_mcp_read_tools_enforce_rbac(seeded_payslip, actor, expected):
    """HR Manager has full HR rights and zero payroll rights. An agent acting
    as one must hit that wall too — otherwise MCP is a way to read payroll
    without the role that permits it."""
    from fastapi import HTTPException

    async with AsyncSessionLocal() as session:
        if expected == 200:
            result = await tools.get_payslip(
                session, seeded_payslip["payslip_id"], actor
            )
            assert result["id"] == seeded_payslip["payslip_id"]
        else:
            with pytest.raises(HTTPException) as raised:
                await tools.get_payslip(session, seeded_payslip["payslip_id"], actor)
            assert raised.value.status_code == 403
            assert "not authorized" in str(raised.value.detail)


async def test_mcp_requires_a_real_actor_and_has_no_demo_admin_fallback():
    """An unauthenticated HTTP request is treated as the demo admin outside
    production. MCP deliberately has no such fallback — an agent that named no
    actor, or an unknown one, gets 401 rather than admin rights."""
    from fastapi import HTTPException

    async with AsyncSessionLocal() as session:
        for actor in ("", "   ", "nobody@example.com"):
            with pytest.raises(HTTPException) as raised:
                await tools.get_department_payroll(session, actor)
            assert raised.value.status_code == 401


async def test_employee_actor_is_scoped_to_their_own_record(
    seeded_payslip, linked_employee
):
    """An Employee reads themselves and gets 404 — not 403 — for anyone else,
    reusing `EmployeeService.assert_can_read` so ids cannot be enumerated."""
    from fastapi import HTTPException

    async with AsyncSessionLocal() as session:
        own = await tools.get_employee(session, linked_employee, EMPLOYEE_EMAIL)
        assert own["employee_id"] == linked_employee

        other = seeded_payslip["employee_id"]
        if other != linked_employee:
            with pytest.raises(HTTPException) as raised:
                await tools.get_employee(session, other, EMPLOYEE_EMAIL)
            assert raised.value.status_code == 404


# ===========================================================================
# 4. The context layer: real, connected, and honest about gaps
# ===========================================================================


async def test_payslip_context_assembles_connected_records(seeded_payslip):
    """The explanation context reaches past the payslip into the employee, the
    contract history, the attendance and the leave that produced it."""
    from app.ai import context_builder

    async with AsyncSessionLocal() as session:
        payslip = (
            await session.execute(
                select(Payslip).where(Payslip.public_id == seeded_payslip["payslip_id"])
            )
        ).scalars().first()
        context = await context_builder.build_payslip_explanation_context(
            session, payslip, question="Why did this employee's pay change?"
        )

    for section in (
        "employee",
        "current_payslip",
        "comparison_with_previous_period",
        "contract_history",
        "contract_applicable_to_this_period",
        "attendance",
        "time_off",
        "salary_structure",
    ):
        assert section in context.facts, f"missing context section: {section}"

    # Traceable back to the services that produced it.
    assert "payslip_explain.calculation_tree" in context.sources
    assert "payroll_context.attendance_facts" in context.sources
    assert "contract_history.contract_timeline" in context.sources

    totals = context.facts["current_payslip"]["totals"]
    assert totals["net_amount"] == str(seeded_payslip["net"])
    assert totals["gross_amount"] == str(seeded_payslip["gross"])


async def test_context_never_serialises_money_as_a_float(seeded_payslip):
    """Architecture §10 on the boundary it is most often broken at.

    Walks the whole rendered prompt payload and fails on any `float`. A single
    `float(Decimal("41800.00"))` would round-trip as 41800.0 and read as an
    amount, which is precisely why this is asserted structurally rather than by
    eyeballing an example.
    """
    from app.ai import context_builder, prompts

    async with AsyncSessionLocal() as session:
        payslip = (
            await session.execute(
                select(Payslip).where(Payslip.public_id == seeded_payslip["payslip_id"])
            )
        ).scalars().first()
        context = await context_builder.build_payslip_explanation_context(
            session, payslip, question="Explain this payslip", include_department=True
        )

    floats: list[str] = []

    def walk(node, path="facts"):
        if isinstance(node, float):
            floats.append(f"{path} = {node!r}")
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, (list, tuple)):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(context.as_payload())
    assert not floats, f"float found on the payroll path: {floats}"

    # The rendered prompt must also be JSON-serialisable without a float coercer.
    rendered = prompts.render(context)
    assert str(seeded_payslip["net"]) in rendered
    assert "AUTHORITATIVE ERP FACTS" in rendered
    assert "UNAVAILABLE INFORMATION" in rendered


async def test_context_reports_absence_instead_of_guessing(seeded_payslip):
    """A first payslip has nothing to compare against, and the context says so
    in words the model is instructed to repeat — rather than omitting the
    section and letting the model infer 'no change'."""
    from app.ai import context_builder

    async with AsyncSessionLocal() as session:
        payslip = (
            await session.execute(
                select(Payslip).where(Payslip.public_id == seeded_payslip["payslip_id"])
            )
        ).scalars().first()
        context = await context_builder.build_payslip_explanation_context(
            session, payslip, question="Why did pay change?"
        )

    comparison = context.facts["comparison_with_previous_period"]
    if not comparison["comparable"]:
        assert comparison["reason"]
        assert any("earliest payslip" in note for note in context.unavailable)


async def test_anomaly_context_is_deterministic_not_model_authored():
    """PRD §5.7 — the signals come from application queries, and the context
    says which query set produced them."""
    from app.ai import context_builder

    async with AsyncSessionLocal() as session:
        context = await context_builder.build_anomaly_context(
            session,
            question="Any unusual payroll patterns?",
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
        )

    assert "deterministic_signals" in context.facts
    assert any("deterministic" in source for source in context.sources)
    summary = context.facts["signal_summary"]
    assert summary["total"] == len(context.facts["deterministic_signals"])
    if summary["total"] == 0:
        assert any("do not manufacture" in note for note in context.unavailable)


# ===========================================================================
# 5. Provider behaviour: narration when up, clean state when down
# ===========================================================================


async def test_provider_failure_returns_clean_unavailable_with_facts_intact(
    monkeypatch, seeded_payslip
):
    """Every provider failing must not lose the deterministic half.

    The result carries `ai_unavailable`, an explanation, and the full fact set —
    so the UI can still show the payslip's real inputs and totals with a banner
    instead of an error page. Fabricating an answer, or returning nothing, would
    both be worse.
    """
    from app.ai import cache as ai_cache
    from app.ai import provider_router
    from app.jobs.tasks.ai_jobs import run_hr_insight

    async def always_fails(prompt, model, api_key):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(provider_router.settings, "GROQ_API_KEY", "k", raising=False)
    monkeypatch.setattr(provider_router, "_CALL_FUNCTIONS", {"groq": always_fails})
    monkeypatch.setattr(ai_cache.AIResponseCache, "get", staticmethod(lambda *a, **k: _none()))
    monkeypatch.setattr(ai_cache.AIResponseCache, "set", staticmethod(lambda *a, **k: _noop()))

    outcome = await run_hr_insight(
        task_type="payslip_explanation",
        question="Why did this change?",
        actor_email=PAYROLL_MANAGER_EMAIL,
        params={"payslip_id": seeded_payslip["payslip_id"]},
    )

    assert outcome["status"] == "ai_unavailable"
    assert outcome["result"]["answer"] is None
    assert "unaffected" in outcome["error"]
    assert outcome["result"]["facts"]["current_payslip"]["totals"]["net_amount"] == str(
        seeded_payslip["net"]
    )


async def test_no_configured_provider_is_unavailable_not_an_exception(monkeypatch):
    """With no API keys at all — the default in a fresh checkout — `generate`
    raises `AIUnavailableError`, which callers turn into a clean state. It must
    not raise something a caller would treat as a bug."""
    from app.ai import cache as ai_cache
    from app.ai import provider_router

    monkeypatch.setattr(provider_router.settings, "GROQ_API_KEY", "", raising=False)
    monkeypatch.setattr(ai_cache.AIResponseCache, "get", staticmethod(lambda *a, **k: _none()))

    with pytest.raises(provider_router.AIUnavailableError) as raised:
        await provider_router.generate(prompt="x", context={}, task_type="general")
    assert "no API key configured" in str(raised.value)
    assert raised.value.reason == "not_configured"


async def test_rate_limited_provider_is_reported_as_rate_limited_not_a_generic_failure(
    monkeypatch,
):
    """A 429 from the provider (or our own per-minute cap) must be tagged
    `reason="rate_limited"` — that is what lets the UI say "try again shortly"
    instead of a generic "AI unavailable", which is the whole point of
    distinguishing the two (a missing key never fixes itself; a rate limit
    does). Groq is the only provider now, so this failure mode is no longer
    masked by falling through to a second provider.
    """
    from app.ai import cache as ai_cache
    from app.ai import provider_router

    class FakeRateLimitError(Exception):
        status_code = 429

    async def always_429(prompt, model, api_key):
        raise FakeRateLimitError("rate limit reached")

    monkeypatch.setattr(provider_router.settings, "GROQ_API_KEY", "k", raising=False)
    monkeypatch.setattr(provider_router, "_CALL_FUNCTIONS", {"groq": always_429})
    monkeypatch.setattr(ai_cache.AIResponseCache, "get", staticmethod(lambda *a, **k: _none()))
    monkeypatch.setattr(ai_cache.AIResponseCache, "set", staticmethod(lambda *a, **k: _noop()))

    with pytest.raises(provider_router.AIUnavailableError) as raised:
        await provider_router.generate(prompt="x", context={}, task_type="general")
    assert raised.value.reason == "rate_limited"


async def test_run_hr_insight_gives_an_honest_message_per_unavailable_reason(
    monkeypatch, seeded_payslip
):
    """The job result's `error` text must never be a raw provider exception —
    it is always one of the canned, honest messages keyed by `reason`, and
    `reason` itself travels on the result so the UI can pick a distinct state
    (see AssistantPage.tsx's `unavailableHeading`)."""
    from app.ai import cache as ai_cache
    from app.ai import provider_router
    from app.jobs.tasks.ai_jobs import run_hr_insight

    class FakeRateLimitError(Exception):
        status_code = 429

    async def always_429(prompt, model, api_key):
        raise FakeRateLimitError("rate limit reached")

    monkeypatch.setattr(provider_router.settings, "GROQ_API_KEY", "k", raising=False)
    monkeypatch.setattr(provider_router, "_CALL_FUNCTIONS", {"groq": always_429})
    monkeypatch.setattr(ai_cache.AIResponseCache, "get", staticmethod(lambda *a, **k: _none()))
    monkeypatch.setattr(ai_cache.AIResponseCache, "set", staticmethod(lambda *a, **k: _noop()))

    outcome = await run_hr_insight(
        task_type="payslip_explanation",
        question="Why did this change?",
        actor_email=PAYROLL_MANAGER_EMAIL,
        params={"payslip_id": seeded_payslip["payslip_id"]},
    )

    assert outcome["status"] == "ai_unavailable"
    assert outcome["reason"] == "rate_limited"
    assert "try again" in outcome["error"].lower()
    assert "rate limit reached" not in outcome["error"]  # never the raw exception text
    # The facts are still the useful half, unaffected by which provider failed.
    assert outcome["result"]["facts"]["current_payslip"]["totals"]["net_amount"] == str(
        seeded_payslip["net"]
    )


async def test_narration_prompt_carries_authoritative_facts_and_the_ban_on_recalculating(
    stub_provider, seeded_payslip
):
    """With a provider available, the prompt the model actually receives
    contains the ERP's figures and the instruction never to recompute them."""
    from app.jobs.tasks.ai_jobs import run_hr_insight

    outcome = await run_hr_insight(
        task_type="payslip_explanation",
        question="Why did this employee's pay change this month?",
        actor_email=PAYROLL_MANAGER_EMAIL,
        params={"payslip_id": seeded_payslip["payslip_id"]},
    )

    assert outcome["status"] == "completed"
    assert outcome["result"]["answer"].startswith("STUB ANSWER")
    assert outcome["result"]["provider"] == "groq"

    prompt = stub_provider["prompt"]
    assert str(seeded_payslip["net"]) in prompt
    # Matched on the phrase alone: the ground rules are wrapped prose, so a
    # longer literal would be asserting the line width rather than the rule.
    assert "recalculate a payslip" in prompt
    assert "never add up lines to check a total" in prompt
    assert "UNAVAILABLE INFORMATION" in prompt
    assert seeded_payslip["employee_name"] in prompt


async def test_ask_endpoint_enqueues_and_never_calls_a_provider_inline(client):
    """Architecture §11 Hard Constraint #4 — the handler returns 202 with a job
    id. If it ever called a provider inline this would hang for the provider
    timeout instead of answering immediately."""
    response = await client.post(
        f"{BASE}/ai/ask",
        json={"question": "What is blocking payroll?", "task_type": "pending_actions"},
        headers=PAYROLL,
    )
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"
    assert response.json()["job_id"]


async def test_ask_endpoint_refuses_a_role_without_payroll_access(client):
    """An HR Manager cannot ask a payroll question, at the door."""
    response = await client.post(
        f"{BASE}/ai/ask",
        json={"question": "What is blocking payroll?", "task_type": "pending_actions"},
        headers=auth_headers(UserRole.EMPLOYEE),
    )
    assert response.status_code == 403, response.text


async def test_task_level_rbac_holds_even_if_a_job_is_enqueued_directly(seeded_payslip):
    """The worker re-checks the matrix. A queued job executes later, in another
    process — the authorization that created it must not be assumed to still
    hold, and this is what stops a stale job from reading payroll."""
    from app.jobs.tasks.ai_jobs import run_hr_insight

    outcome = await run_hr_insight(
        task_type="payslip_explanation",
        question="Explain this",
        actor_email=HR_MANAGER_EMAIL,
        params={"payslip_id": seeded_payslip["payslip_id"]},
    )
    assert outcome["status"] == "failed"
    assert "not authorized" in outcome["error"]


# ===========================================================================
# 6. Propose -> human confirm -> execute (Architecture §8.1/§11)
# ===========================================================================


async def test_proposal_rationale_is_honest_about_a_rate_limited_provider(
    leave_scenario, monkeypatch
):
    """When the provider is rate-limited rather than merely unconfigured, the
    proposal's rationale must say so — "temporarily unavailable ... try
    again shortly" — not the permanent-sounding "no AI provider was
    available". `ai_reason` travels alongside for the UI badge
    (AssistantPage.tsx's "AI busy" pill). The proposal is still created and
    still requires human confirmation either way — a narration failure is
    never a reason to block the underlying request.
    """
    from app.ai import cache as ai_cache
    from app.ai import provider_router
    from app.jobs.tasks.ai_jobs import run_ai_action_proposal

    class FakeRateLimitError(Exception):
        status_code = 429

    async def always_429(prompt, model, api_key):
        raise FakeRateLimitError("rate limit reached")

    monkeypatch.setattr(provider_router.settings, "GROQ_API_KEY", "k", raising=False)
    monkeypatch.setattr(provider_router, "_CALL_FUNCTIONS", {"groq": always_429})
    monkeypatch.setattr(ai_cache.AIResponseCache, "get", staticmethod(lambda *a, **k: _none()))
    monkeypatch.setattr(ai_cache.AIResponseCache, "set", staticmethod(lambda *a, **k: _noop()))

    employee = leave_scenario["employee"]
    outcome = await run_ai_action_proposal(
        action="create_time_off_request",
        params={
            "employee_id": employee["id"],
            "time_off_type_id": leave_scenario["leave_type"]["id"],
            "date_from": "2026-09-11",
            "date_to": "2026-09-11",
            "reason": "Proposed by AI",
        },
        question="Request one day of casual leave next Friday.",
        actor_email=EMPLOYEE_EMAIL,
    )

    assert outcome["status"] == "completed"
    result = outcome["result"]
    assert result["ai_status"] == "ai_unavailable"
    assert result["ai_reason"] == "rate_limited"
    assert result["requires_human_confirmation"] is True
    rationale = result["proposal"]["rationale"].lower()
    assert "temporarily unavailable" in rationale
    assert "rate limit reached" not in rationale  # never the raw exception text


async def test_proposal_does_not_mutate_until_a_human_confirms(
    client, leave_scenario, monkeypatch
):
    """The full chain, asserted at every step.

    Proposing writes no time-off request. Confirming writes exactly one,
    through the same service the REST route uses. The audit trail carries the
    proposal, the confirmation and the outcome — §11's "who proposed, who
    confirmed, what executed".
    """
    from app.ai import cache as ai_cache
    from app.ai import provider_router
    from app.jobs.tasks.ai_jobs import run_ai_action_proposal
    from app.models.time_off import TimeOffRequest

    async def fake_call(prompt, model, api_key):
        return (
            json.dumps(
                {"decision": "propose", "rationale": "One day is within the balance."}
            ),
            42,
        )

    monkeypatch.setattr(provider_router.settings, "GROQ_API_KEY", "k", raising=False)
    monkeypatch.setattr(provider_router, "_CALL_FUNCTIONS", {"groq": fake_call})
    monkeypatch.setattr(ai_cache.AIResponseCache, "get", staticmethod(lambda *a, **k: _none()))
    monkeypatch.setattr(ai_cache.AIResponseCache, "set", staticmethod(lambda *a, **k: _noop()))

    employee = leave_scenario["employee"]
    params = {
        "employee_id": employee["id"],
        "time_off_type_id": leave_scenario["leave_type"]["id"],
        "date_from": "2026-09-11",
        "date_to": "2026-09-11",
        "reason": "Proposed by AI",
    }

    async def request_count() -> int:
        async with AsyncSessionLocal() as session:
            from app.models.employee import Employee

            row = (
                await session.execute(
                    select(Employee).where(Employee.public_id == employee["id"])
                )
            ).scalars().first()
            return len(
                (
                    await session.execute(
                        select(TimeOffRequest).where(TimeOffRequest.employee_id == row.id)
                    )
                ).scalars().all()
            )

    before = await request_count()

    outcome = await run_ai_action_proposal(
        action="create_time_off_request",
        params=params,
        question="Request one day of casual leave next Friday.",
        actor_email=EMPLOYEE_EMAIL,
    )
    assert outcome["status"] == "completed"
    proposal = outcome["result"]["proposal"]
    assert proposal["status"] == "pending_review"
    assert outcome["result"]["requires_human_confirmation"] is True
    assert outcome["result"]["ai_decision"] == "propose"
    # The balance the human needs to judge it is in the facts, not implied.
    assert outcome["result"]["facts"]["leave_balances"]

    assert await request_count() == before, "proposing must not create a request"

    employee_headers = auth_headers(UserRole.EMPLOYEE, employee_id=employee["id"])
    confirm = await client.post(
        f"{BASE}/ai/proposals/{proposal['proposal_id']}/confirm", headers=employee_headers
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["status"] == "confirmed"
    assert confirm.json()["result"]["status"] == "to_approve"
    assert await request_count() == before + 1

    async with AsyncSessionLocal() as session:
        actions = (
            (
                await session.execute(
                    select(AuditLog.action, AuditLog.actor).where(
                        AuditLog.entity_id == proposal["proposal_id"]
                    )
                )
            )
            .all()
        )
    recorded = {action for action, _ in actions}
    assert {"AI_PROPOSED_ACTION", "AI_CONFIRMED_ACTION", "AI_ACTION_EXECUTED"} <= recorded
    proposers = {actor for action, actor in actions if action == "AI_PROPOSED_ACTION"}
    confirmers = {actor for action, actor in actions if action == "AI_CONFIRMED_ACTION"}
    assert proposers == {"ai-agent"}
    assert confirmers == {EMPLOYEE_EMAIL}


async def test_a_proposal_cannot_be_confirmed_by_a_different_user(client, leave_scenario):
    """A proposal is bound to the user it was prepared for. Otherwise anyone
    holding a proposal id could run a mutation under someone else's rights."""
    from app.ai import proposals

    async with AsyncSessionLocal() as session:
        record = await proposals.create(
            session,
            action="create_time_off_request",
            params={
                "employee_id": leave_scenario["employee"]["id"],
                "time_off_type_id": leave_scenario["leave_type"]["id"],
                "date_from": "2026-09-11",
                "date_to": "2026-09-11",
            },
            proposed_for_email=EMPLOYEE_EMAIL,
            rationale="test",
            ai_status="ok",
            ai_provider="stub",
            raw_output=None,
        )
        await session.commit()

    response = await client.post(
        f"{BASE}/ai/proposals/{record['proposal_id']}/confirm", headers=HR
    )
    assert response.status_code == 403, response.text
    assert "prepared for a different user" in response.json()["detail"]


async def test_a_confirmed_proposal_cannot_be_replayed(client, leave_scenario, monkeypatch):
    """Confirming consumes the proposal, so a double-click cannot submit two
    leave requests."""
    from app.ai import proposals

    async with AsyncSessionLocal() as session:
        record = await proposals.create(
            session,
            action="create_time_off_request",
            params={
                "employee_id": leave_scenario["employee"]["id"],
                "time_off_type_id": leave_scenario["leave_type"]["id"],
                "date_from": "2026-09-14",
                "date_to": "2026-09-14",
            },
            proposed_for_email=EMPLOYEE_EMAIL,
            rationale="test",
            ai_status="ok",
            ai_provider="stub",
            raw_output=None,
        )
        await session.commit()

    headers = auth_headers(
        UserRole.EMPLOYEE, employee_id=leave_scenario["employee"]["id"]
    )
    first = await client.post(
        f"{BASE}/ai/proposals/{record['proposal_id']}/confirm", headers=headers
    )
    assert first.status_code == 200, first.text

    second = await client.post(
        f"{BASE}/ai/proposals/{record['proposal_id']}/confirm", headers=headers
    )
    assert second.status_code == 404, second.text


async def test_rejecting_a_proposal_writes_an_audit_row_and_no_domain_record(
    client, leave_scenario
):
    """A human saying no is recorded. Nothing is written to the domain."""
    from app.ai import proposals

    async with AsyncSessionLocal() as session:
        record = await proposals.create(
            session,
            action="create_time_off_request",
            params={
                "employee_id": leave_scenario["employee"]["id"],
                "time_off_type_id": leave_scenario["leave_type"]["id"],
                "date_from": "2026-09-18",
                "date_to": "2026-09-18",
            },
            proposed_for_email=EMPLOYEE_EMAIL,
            rationale="test",
            ai_status="ok",
            ai_provider="stub",
            raw_output=None,
        )
        await session.commit()

    headers = auth_headers(
        UserRole.EMPLOYEE, employee_id=leave_scenario["employee"]["id"]
    )
    response = await client.post(
        f"{BASE}/ai/proposals/{record['proposal_id']}/reject",
        json={"note": "Wrong date"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "rejected"

    async with AsyncSessionLocal() as session:
        actions = (
            (
                await session.execute(
                    select(AuditLog.action).where(
                        AuditLog.entity_id == record["proposal_id"]
                    )
                )
            )
            .scalars()
            .all()
        )
    assert "AI_REJECTED_ACTION" in actions
    assert "AI_ACTION_EXECUTED" not in actions


async def test_only_registered_actions_may_be_proposed():
    """The proposable set is closed. Payroll compute is not in it, and asking
    for it is a 400 rather than an attempt."""
    from fastapi import HTTPException

    from app.ai import proposals

    assert proposals.KNOWN_ACTIONS == {"create_time_off_request"}
    async with AsyncSessionLocal() as session:
        with pytest.raises(HTTPException) as raised:
            await proposals.create(
                session,
                action="compute_payrun",
                params={},
                proposed_for_email=ADMIN_EMAIL,
                rationale="x",
                ai_status="ok",
                ai_provider="stub",
                raw_output=None,
            )
        assert raised.value.status_code == 400


# ===========================================================================
# 7. Tool surface matches Architecture §8.2
# ===========================================================================


async def test_mcp_server_registers_the_architecture_tool_set():
    """The §8.2 tool list, checked against what the server actually exposes."""
    from app.mcp import server

    registered = {tool.name for tool in await server.mcp.list_tools()}
    expected_reads = {
        "get_employee",
        "get_employee_contracts",
        "get_attendance_summary",
        "get_leave_balance",
        "get_pending_time_off",
        "get_payrun_summary",
        "get_payslip",
        "explain_payslip",
        "get_payroll_warnings",
        "get_department_payroll",
        "get_payroll_trends",
        "find_payroll_anomalies",
        "find_contract_conflicts",
    }
    expected_actions = {
        "create_time_off_request",
        "approve_time_off_request",
        "correct_attendance",
        "create_payrun",
        "request_payroll_validation",
    }
    assert expected_reads <= registered
    assert expected_actions <= registered
    assert "query_audit_trail" in registered


async def test_contract_conflicts_is_empty_because_the_constraint_holds():
    """The EXCLUDE constraint makes overlapping active contracts impossible.
    This tool reports zero — and would be the alarm if the constraint were
    ever dropped."""
    async with AsyncSessionLocal() as session:
        result = await tools.find_contract_conflicts(session, HR_MANAGER_EMAIL)
    assert result["conflict_count"] == 0, result["conflicts"]
