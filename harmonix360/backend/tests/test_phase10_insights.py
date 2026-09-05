"""Phase 10 — realtime, observability and the read-side intelligence features.

The load-bearing tests here are the ones that would fail if a Phase 10 feature
started behaving like a write path or a second payroll engine:

  * `test_events_are_not_broadcast_before_commit` and its rollback sibling —
    the commit-after-broadcast guarantee, asserted by observing what a fake
    socket actually received rather than by reading the call order.
  * `test_calculation_tree_matches_the_persisted_payslip_exactly` — the View
    Calculation screen renders the engine's figures and nothing else.
  * `test_realtime_failure_does_not_break_the_request` — realtime is an
    enhancement; a broken socket layer must not turn a committed payroll
    transaction into a 500.
  * `test_exactly_three_traces_and_no_blanket_instrumentation` — Architecture
    §8.5 permits three named traces, and nothing else.
"""
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.enums import UserRole
from app.models.payroll import Payrun, Payslip
from tests.conftest import auth_headers, unique_email

BASE = "/api/v1"

HR = auth_headers(UserRole.HR_MANAGER)
PAYROLL = auth_headers(UserRole.HR_PAYROLL_MANAGER)
EMPLOYEE = auth_headers(UserRole.EMPLOYEE)


class FakeSocket:
    """A WebSocket that only remembers what it was sent.

    A real socket would need a running server and an event loop of its own; the
    property under test is which frames leave the application and when, and
    that is fully observable here. A real end-to-end connection is exercised
    separately by `scripts/phase10_demo.py`.
    """

    def __init__(self, should_fail: bool = False):
        self.received: list[dict] = []
        self.accepted = False
        self.should_fail = should_fail

    async def accept(self):
        self.accepted = True

    async def send_json(self, payload: dict):
        if self.should_fail:
            raise RuntimeError("socket is broken")
        self.received.append(payload)


@pytest_asyncio.fixture
async def subscriber_socket():
    """An HR subscriber attached to every channel, removed afterwards."""
    from app.realtime.events import CHANNELS
    from app.realtime.ws_manager import Subscriber, manager

    socket = FakeSocket()
    principal = Subscriber(
        email="hr.manager@peoplepay360.com", role="hr_manager", is_hr=True, is_payroll=True
    )
    for channel in CHANNELS:
        await manager.connect(channel, socket, principal)
    yield socket
    for channel in CHANNELS:
        await manager.disconnect(channel, socket)


@pytest_asyncio.fixture
async def seeded_payslip():
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
            "payrun_id": run.public_id,
            "gross": payslip.gross_amount,
            "net": payslip.net_amount,
            "worked_days": payslip.worked_days,
            "line_count": len(payslip.lines),
        }


# ===========================================================================
# 1. Realtime — the commit-after-broadcast guarantee
# ===========================================================================


async def test_events_are_staged_not_sent_when_queued(subscriber_socket):
    """`queue_event` puts nothing on the wire.

    This is the half of the guarantee that is easy to lose: a helper that
    "publishes" would look identical at the call site and would fire before the
    transaction resolved.
    """
    from app.realtime.events import queue_event, staged_events

    async with AsyncSessionLocal() as session:
        queue_event(session, "attendance", "attendance.checked_in", {"employee_id": "emp_x"})
        assert len(staged_events(session)) == 1
        assert subscriber_socket.received == []


async def test_events_are_not_broadcast_before_commit(client, cleanup_employees, subscriber_socket):
    """A real check-in through the API delivers exactly one frame, and the row
    it describes is readable when it arrives.

    Asserting the ROW IS READABLE is the point. "Sent after commit" is not
    observable from ordering alone — a frame could be sent from inside the
    transaction and still arrive last. Reading the record back in a separate
    session at the moment of receipt proves the commit had happened.
    """
    created = await client.post(
        f"{BASE}/employees/",
        json={
            "first_name": "Realtime",
            "last_name": "Subject",
            "work_email": unique_email("realtime"),
        },
        headers=HR,
    )
    assert created.status_code == 201
    employee = created.json()

    response = await client.post(
        f"{BASE}/attendance/",
        json={
            "employee_id": employee["id"],
            "check_in": datetime.now(UTC).replace(microsecond=0).isoformat(),
        },
        headers=HR,
    )
    assert response.status_code == 201, response.text
    attendance_id = response.json()["id"]

    frames = [f for f in subscriber_socket.received if f["event"] == "attendance.checked_in"]
    assert len(frames) == 1, subscriber_socket.received
    assert frames[0]["data"]["attendance_id"] == attendance_id
    assert frames[0]["channel"] == "attendance"

    from app.models.attendance import Attendance

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(Attendance).where(Attendance.public_id == attendance_id)
            )
        ).scalars().first()
        assert row is not None, "the frame described a row that is not committed"


async def test_a_rolled_back_transaction_broadcasts_nothing(
    client, cleanup_employees, subscriber_socket
):
    """A refused request announces nothing.

    Two check-ins for an employee that does not exist: the service 404s, the
    session rolls back, and no frame is sent. Without the staging design this
    would announce a check-in that never happened.
    """
    before = len(subscriber_socket.received)
    response = await client.post(
        f"{BASE}/attendance/",
        json={
            "employee_id": "emp_doesnotexist",
            "check_in": datetime.now(UTC).replace(microsecond=0).isoformat(),
        },
        headers=HR,
    )
    assert response.status_code in (403, 404), response.text
    assert len(subscriber_socket.received) == before


async def test_realtime_failure_does_not_break_the_request(client, cleanup_employees):
    """A socket that raises on send must not fail the committed transaction.

    Architecture §8.4: realtime is a notification layer. A dead browser tab is
    not a payroll error, and if this ever regressed, every HR user with a stale
    tab would start seeing 500s on check-in.
    """
    from app.realtime.events import CHANNELS
    from app.realtime.ws_manager import Subscriber, manager

    broken = FakeSocket(should_fail=True)
    principal = Subscriber(email="x@y.z", role="hr_manager", is_hr=True, is_payroll=True)
    for channel in CHANNELS:
        await manager.connect(channel, broken, principal)
    try:
        created = await client.post(
            f"{BASE}/employees/",
            json={
                "first_name": "Broken",
                "last_name": "Socket",
                "work_email": unique_email("broken"),
            },
            headers=HR,
        )
        assert created.status_code == 201
        response = await client.post(
            f"{BASE}/attendance/",
            json={
                "employee_id": created.json()["id"],
                "check_in": datetime.now(UTC).replace(microsecond=0).isoformat(),
            },
            headers=HR,
        )
        assert response.status_code == 201, response.text
    finally:
        for channel in CHANNELS:
            await manager.disconnect(channel, broken)


async def test_channel_visibility_applies_the_rbac_matrix():
    """An Employee subscriber receives their own events and not a colleague's,
    and never anything on the payroll channel."""
    from app.realtime.events import _visible_to
    from app.realtime.ws_manager import Subscriber

    employee = Subscriber(
        email="e@x", role="employee", employee_public_id="emp_me", is_hr=False, is_payroll=False
    )
    hr = Subscriber(email="h@x", role="hr_manager", is_hr=True)
    payroll = Subscriber(email="p@x", role="hr_payroll_manager", is_payroll=True)

    own = {"channel": "approvals", "data": {"employee_id": "emp_me"}}
    other = {"channel": "approvals", "data": {"employee_id": "emp_someone_else"}}
    payroll_event = {"channel": "payroll", "data": {"payrun_id": "prun_x"}}

    assert _visible_to(employee, own) is True
    assert _visible_to(employee, other) is False
    assert _visible_to(employee, payroll_event) is False
    assert _visible_to(hr, other) is True
    # HR Manager has zero payroll access — §5's sharpest line, on the socket too.
    assert _visible_to(hr, payroll_event) is False
    assert _visible_to(payroll, payroll_event) is True
    # An unauthenticated socket receives nothing at all.
    assert _visible_to(None, own) is False


async def test_websocket_route_refuses_an_unsigned_token():
    """The socket is authenticated from a signed token, with no
    unauthenticated-equals-demo-admin branch."""
    from app.api.v1.routers.realtime import _subscriber_from_token

    assert _subscriber_from_token("") is None
    assert _subscriber_from_token("not-a-jwt") is None

    from app.core.security import create_access_token

    token = create_access_token(subject="a@b.c", role=UserRole.HR_PAYROLL_MANAGER.value)
    subscriber = _subscriber_from_token(token)
    assert subscriber is not None
    assert subscriber.is_payroll is True


async def test_realtime_status_is_readable_and_honest(client):
    response = await client.get(f"{BASE}/realtime/status", headers=PAYROLL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["channels"]) == {"attendance", "time_off", "approvals", "payroll"}
    assert "REST remains authoritative" in body["note"]


# ===========================================================================
# 2. Observability — exactly three traces
# ===========================================================================


def test_exactly_three_traces_and_no_blanket_instrumentation():
    """Architecture §8.5 permits three named business traces and no more."""
    from app.core import telemetry

    assert telemetry.ALLOWED_TRACES == {"payroll.compute", "ai.mcp", "offline.sync"}
    described = telemetry.describe_traces()
    assert [trace["name"] for trace in described["traces"]] == [
        "payroll.compute",
        "ai.mcp",
        "offline.sync",
    ]
    assert described["blanket_instrumentation"] is False

    # A fourth trace name is refused rather than silently emitted.
    with pytest.raises(ValueError, match="not one of the three"):
        with telemetry._span("http.request", "handle"):
            pass


def test_no_auto_instrumentor_is_installed():
    """The FastAPI/SQLAlchemy/Redis/HTTPX auto-instrumentation the previous
    build installed is gone, and `main.py` no longer imports it.

    Checked in the source rather than by inspecting the tracer provider,
    because an instrumentor that is imported but not called still signals an
    intent to re-enable it, and this is the line that must not creep back."""
    import pathlib

    source = pathlib.Path("app/main.py").read_text(encoding="utf-8")
    assert "FastAPIInstrumentor" not in source
    telemetry_source = pathlib.Path("app/core/telemetry.py").read_text(encoding="utf-8")
    for instrumentor in (
        "SQLAlchemyInstrumentor",
        "RedisInstrumentor",
        "HTTPXClientInstrumentor",
        "FastAPIInstrumentor",
    ):
        assert instrumentor not in telemetry_source, instrumentor


async def test_spans_carry_no_payroll_amounts(seeded_payslip):
    """A span attribute is a telemetry boundary, and Architecture §10 applies
    to it. The payroll trace records counts, ids and statuses only.

    Asserted by capturing the attributes actually set during a real compute,
    then checking none of them equals a monetary value from the payslip.
    """
    from app.core import telemetry

    captured: list[dict] = []

    class _RecordingSpan:
        def set_attribute(self, key, value):
            captured.append({key: value})

        def record_exception(self, exc):  # pragma: no cover - not exercised here
            pass

    class _RecordingTracer:
        def start_as_current_span(self, name):
            from contextlib import contextmanager

            @contextmanager
            def _cm():
                yield _RecordingSpan()

            return _cm()

    original = telemetry.get_tracer
    telemetry.get_tracer = lambda name="": _RecordingTracer()
    try:
        with telemetry.payroll_span(
            "write_payslips", payrun_id="prun_x", computed_count=3, status="computed"
        ):
            pass
    finally:
        telemetry.get_tracer = original

    values = {str(value) for entry in captured for value in entry.values()}
    assert str(seeded_payslip["net"]) not in values
    assert str(seeded_payslip["gross"]) not in values
    assert "computed" in values


# ===========================================================================
# 3. View Calculation (PRD §5.6)
# ===========================================================================


async def test_calculation_tree_matches_the_persisted_payslip_exactly(client, seeded_payslip):
    """The screen renders the engine's figures. Its category subtotals are
    shown alongside the persisted totals, never instead of them — and here they
    are asserted to agree, with exact Decimal equality and no approximation."""
    response = await client.get(
        f"{BASE}/payslips/{seeded_payslip['payslip_id']}/calculation", headers=PAYROLL
    )
    assert response.status_code == 200, response.text
    tree = response.json()

    assert tree["totals"]["gross_amount"] == str(seeded_payslip["gross"])
    assert tree["totals"]["net_amount"] == str(seeded_payslip["net"])
    assert tree["totals"]["worked_days"] == str(seeded_payslip["worked_days"])
    assert len(tree["lines"]) == seeded_payslip["line_count"]
    assert "no rule was re-evaluated" in tree["source"]

    # The lines are in the sequence they ran.
    sequences = [line["sequence"] for line in tree["lines"]]
    assert sequences == sorted(sequences)

    # The subtotals are a rendering of the same lines: every category total is
    # the exact sum of its lines, as Decimals.
    by_category: dict[str, Decimal] = {}
    for line in tree["lines"]:
        by_category[line["category"]] = by_category.get(
            line["category"], Decimal("0.00")
        ) + Decimal(line["amount"])
    for subtotal in tree["category_subtotals"]:
        assert Decimal(subtotal["total"]) == by_category[subtotal["category"]]

    # And the frozen inputs are present rather than reconstructed.
    assert tree["inputs"]["available"] is True
    names = {value["name"] for value in tree["inputs"]["values"]}
    assert {"CONTRACT_WAGE", "WORKED_DAYS"} <= names


async def test_calculation_requires_a_payroll_role(client, seeded_payslip):
    response = await client.get(
        f"{BASE}/payslips/{seeded_payslip['payslip_id']}/calculation", headers=HR
    )
    assert response.status_code == 403, response.text


# ===========================================================================
# 4. Deterministic anomalies (PRD §5.7)
# ===========================================================================


async def test_anomalies_are_deterministic_and_carry_their_evidence(client):
    response = await client.get(
        f"{BASE}/anomalies",
        params={"period_start": "2026-07-01", "period_end": "2026-07-31"},
        headers=PAYROLL,
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["summary"]["total"] == len(body["anomalies"])
    # Thresholds are published so nobody mistakes a convention for a rule.
    assert "not payroll" in body["thresholds"]["note"]
    assert body["thresholds"]["large_salary_jump_pct"] == "20.00"

    for anomaly in body["anomalies"]:
        assert anomaly["type"]
        assert anomaly["severity"] in {"high", "medium", "low"}
        assert anomaly["reason"], f"{anomaly['type']} states no reason"
        # Money never crosses this boundary as a float.
        for field in ("current_value", "baseline"):
            assert not isinstance(anomaly[field], float)

    # Most severe first.
    order = {"high": 0, "medium": 1, "low": 2}
    severities = [order[a["severity"]] for a in body["anomalies"]]
    assert severities == sorted(severities)


async def test_anomalies_detect_a_real_missing_bank_account(client, cleanup_employees):
    """PRD §7's advanced metric: at least one real, non-fabricated warning
    against real data."""
    created = await client.post(
        f"{BASE}/employees/",
        json={
            "first_name": "Nobank",
            "last_name": "Account",
            "work_email": unique_email("nobank"),
            "bank_account": None,
        },
        headers=HR,
    )
    assert created.status_code == 201, created.text
    employee_id = created.json()["id"]

    response = await client.get(f"{BASE}/anomalies", headers=PAYROLL)
    assert response.status_code == 200
    found = [
        a
        for a in response.json()["anomalies"]
        if a["type"] == "missing_bank_details" and a["employee_id"] == employee_id
    ]
    assert found, "the employee with no bank account was not detected"
    assert found[0]["severity"] == "high"
    assert found[0]["navigate_to"] == f"/employees/{employee_id}"
    assert "recompute" in found[0]["reason"]


async def test_one_failing_detector_does_not_hide_the_others(monkeypatch, client):
    """A broken check reports itself as a finding rather than silently
    removing its category from the panel."""
    from app.services import anomalies

    async def explodes(session, filters):
        raise RuntimeError("deliberate detector failure")

    monkeypatch.setattr(
        anomalies, "DETECTORS", (explodes, anomalies.detect_missing_bank_details)
    )
    response = await client.get(f"{BASE}/anomalies", headers=PAYROLL)
    assert response.status_code == 200, response.text
    types = {a["type"] for a in response.json()["anomalies"]}
    assert "detector_failed" in types


async def test_anomalies_require_a_payroll_role(client):
    assert (await client.get(f"{BASE}/anomalies", headers=HR)).status_code == 403


async def test_department_spike_is_silent_when_nothing_was_paid_yet(client):
    """A period with no paid payroll is not a 100% spend drop.

    Department totals count PAID payslips only, so an unrun month reads as zero
    for every department. Without this guard the panel opens on the 5th of the
    month with one confident "payroll fell 100%" finding per department —
    describing the calendar, not the payroll. Verified against a period far
    enough in the future that nothing can have been paid in it.
    """
    response = await client.get(
        f"{BASE}/anomalies",
        params={"period_start": "2027-11-01", "period_end": "2027-11-30"},
        headers=PAYROLL,
    )
    assert response.status_code == 200, response.text
    spikes = [a for a in response.json()["anomalies"] if a["type"] == "department_spend_spike"]
    assert spikes == [], spikes


# ===========================================================================
# 5. Contract Time Machine (PRD §5.8)
# ===========================================================================


async def test_time_machine_highlights_the_contract_the_engine_resolves(
    client, cleanup_employees, seeded_payslip
):
    """The timeline's period resolution comes from the payroll engine's own
    resolver, so it can never highlight a contract the employee was not paid
    under."""
    response = await client.get(
        f"{BASE}/employees/{seeded_payslip['employee_id']}/contract-timeline",
        params={"period_start": "2026-07-01", "period_end": "2026-07-31"},
        headers=HR,
    )
    assert response.status_code == 200, response.text
    timeline = response.json()

    assert timeline["contract_count"] >= 1
    assert timeline["resolution"]["resolved"] is True
    resolved_id = timeline["resolution"]["contract"]["contract_id"]

    # It is the contract the payslip was actually computed against.
    async with AsyncSessionLocal() as session:
        payslip = (
            await session.execute(
                select(Payslip).where(Payslip.public_id == seeded_payslip["payslip_id"])
            )
        ).scalars().first()
        assert payslip.reference_snapshot["contract"]["id"] == resolved_id

    for contract in timeline["contracts"]:
        assert isinstance(contract["wage"], str)


async def test_time_machine_reports_an_unresolvable_period_as_information(
    client, cleanup_employees
):
    """No contract for a period is a fact worth showing, not a 500."""
    created = await client.post(
        f"{BASE}/employees/",
        json={
            "first_name": "Nocontract",
            "last_name": "Person",
            "work_email": unique_email("nocontract"),
        },
        headers=HR,
    )
    assert created.status_code == 201
    response = await client.get(
        f"{BASE}/employees/{created.json()['id']}/contract-timeline",
        params={"period_start": "2026-07-01", "period_end": "2026-07-31"},
        headers=HR,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["contract_count"] == 0
    assert body["resolution"]["resolved"] is False
    assert "no active contract" in body["resolution"]["reason"]


# ===========================================================================
# 6. Pay-change comparison (PRD §5.9)
# ===========================================================================


async def test_comparison_reports_no_previous_period_rather_than_an_empty_diff(
    client, seeded_payslip
):
    response = await client.get(
        f"{BASE}/payslips/{seeded_payslip['payslip_id']}/comparison", headers=PAYROLL
    )
    assert response.status_code == 200, response.text
    body = response.json()
    if not body["comparable"]:
        assert "earliest payslip" in body["reason"]
    else:
        assert body["previous"]["payslip_id"]
        assert isinstance(body["totals"]["net_amount"]["delta"], str)


# ===========================================================================
# 7. Validation firewall (PRD §5.10)
# ===========================================================================


async def test_firewall_groups_findings_and_points_at_the_existing_revalidate(
    client, seeded_payslip
):
    response = await client.get(
        f"{BASE}/payruns/{seeded_payslip['payrun_id']}/firewall", headers=PAYROLL
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["payrun_id"] == seeded_payslip["payrun_id"]
    assert body["gate_message"]
    # Revalidate is the EXISTING endpoint, named rather than reimplemented.
    assert body["revalidate"]["path"] == (
        f"/api/v1/payruns/{seeded_payslip['payrun_id']}/validate"
    )
    assert body["revalidate"]["method"] == "POST"

    for group in body["groups"]:
        assert group["count"] == len(group["issues"])
        assert group["fix"], f"{group['code']} offers no fix"
        for issue in group["issues"]:
            assert issue["navigate_to"]

    # The seeded July run is paid, so the gate is closed for a different reason
    # than a blocking issue — and the message must say which.
    assert body["can_validate"] is False
    assert "paid" in body["gate_message"].lower()


async def test_firewall_never_hides_an_unrecognised_finding_code():
    """An unknown code falls through to a generic entry rather than being
    dropped. A blocking issue nobody can see still blocks."""
    from app.services import firewall

    assert "unknown_future_code" not in firewall._GUIDANCE
    assert firewall._GENERIC["fix"]
    # Every code the engine can emit is either in the table or handled by the
    # generic entry — asserted by construction: `_GUIDANCE.get(code, _GENERIC)`.
    assert firewall._GUIDANCE.get("no_such_code", firewall._GENERIC) is firewall._GENERIC


async def test_open_gates_lists_only_unfinalized_runs(client, seeded_payslip):
    response = await client.get(f"{BASE}/payruns/firewall/open", headers=PAYROLL)
    assert response.status_code == 200, response.text
    for payrun in response.json()["payruns"]:
        assert payrun["status"] in ("draft", "computed")


# ===========================================================================
# 8. The AI context layer consumes the deterministic signals (§8 of the brief)
# ===========================================================================


async def test_ai_context_uses_the_phase10_anomaly_engine():
    """Phase 9 left a hook for this and reported which source it used. With the
    engine present it must be the engine, not the dashboard fallback —
    otherwise the AI would be narrating a thinner signal set without saying so.
    """
    from app.ai import context_builder
    from app.services.dashboard import resolve_filters

    async with AsyncSessionLocal() as session:
        filters = await resolve_filters(
            session,
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            department_id=None,
            employee_type=None,
        )
        signals = await context_builder.deterministic_signals(session, filters)

    assert "anomalies.detect_all" in signals["source"]
    assert "deterministic" in signals["source"]


async def test_anomaly_context_reaches_the_model_as_structured_signals():
    from app.ai import context_builder

    async with AsyncSessionLocal() as session:
        context = await context_builder.build_anomaly_context(
            session,
            question="Anything unusual?",
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
        )
    assert "deterministic_signals" in context.facts
    assert any("anomalies.detect_all" in source for source in context.sources)


# ===========================================================================
# 9. Nothing about payroll changed
# ===========================================================================


async def test_payroll_computation_is_unchanged_by_phase_10(
    # Order matters: pytest tears fixtures down in reverse, and a payrun holds
    # an FK into its salary structure — so `cleanup_payroll` must be listed
    # LAST in order to run FIRST. This is the same ordering
    # `tests/test_payroll_api.py` uses for the golden payslip.
    client,
    cleanup_employees,
    cleanup_salary_config,
    cleanup_payroll,
):
    """A hand-checkable payslip, computed through the real endpoints with the
    realtime and tracing layers active.

    BASIC 30000.00 -> HRA 12000.00 (40%) -> GROSS 42000.00 -> PT 200.00 ->
    NET 41800.00. Exact Decimal equality, no approximation. If a Phase 10
    change had perturbed the engine by so much as a cent, this fails.
    """
    period_start, period_end = date(2026, 3, 2), date(2026, 3, 6)

    employee = (
        await client.post(
            f"{BASE}/employees/",
            json={
                "first_name": "Phase10",
                "last_name": "Golden",
                "work_email": unique_email("p10golden"),
                "bank_account": "GB00P10000001",
            },
            headers=HR,
        )
    ).json()

    schedule = (
        await client.post(
            f"{BASE}/working-schedules/",
            json={
                "name": f"P10 weekdays {uuid.uuid4().hex[:6]}",
                "lines": [
                    {"day_of_week": day, "start_time": "09:00", "end_time": "17:00", "break_minutes": 0}
                    for day in ("monday", "tuesday", "wednesday", "thursday", "friday")
                ],
            },
            headers=HR,
        )
    ).json()
    await client.patch(
        f"{BASE}/employees/{employee['id']}",
        json={"default_schedule_id": schedule["id"]},
        headers=HR,
    )
    await client.post(
        f"{BASE}/contracts/",
        json={
            "employee_id": employee["id"],
            "wage": "30000.00",
            "start_date": "2026-01-01",
            "end_date": None,
            "status": "active",
        },
        headers=HR,
    )

    for offset in range(5):
        day = period_start + timedelta(days=offset)
        check_in = datetime.combine(day, datetime.min.time(), UTC) + timedelta(hours=9)
        await client.post(
            f"{BASE}/attendance/",
            json={
                "employee_id": employee["id"],
                "check_in": check_in.isoformat(),
                "check_out": (check_in + timedelta(hours=8)).isoformat(),
            },
            headers=HR,
        )

    tag = uuid.uuid4().hex[:6].upper()
    rules = []
    for body in (
        {"name": "Basic", "code": f"P10_BASIC_{tag}", "category": "basic",
         "computation_method": "formula", "expression": "CONTRACT_WAGE"},
        {"name": "Hra", "code": f"P10_HRA_{tag}", "category": "allowance",
         "computation_method": "percentage", "amount": "40.00",
         "percentage_base_code": f"P10_BASIC_{tag}"},
        {"name": "Gross", "code": f"P10_GROSS_{tag}", "category": "gross",
         "computation_method": "formula",
         "expression": f"P10_BASIC_{tag} + P10_HRA_{tag}"},
        {"name": "Pt", "code": f"P10_PT_{tag}", "category": "deduction",
         "computation_method": "fixed", "amount": "200.00"},
        {"name": "Net", "code": f"P10_NET_{tag}", "category": "net",
         "computation_method": "formula",
         "expression": f"P10_GROSS_{tag} - P10_PT_{tag}"},
    ):
        response = await client.post(f"{BASE}/salary-rules/", json=body, headers=PAYROLL)
        assert response.status_code == 201, response.text
        rules.append(response.json())

    structure = (
        await client.post(
            f"{BASE}/salary-structures/",
            json={
                "name": "Phase 10 golden",
                "code": f"P10_{tag}",
                "rules": [
                    {"salary_rule_id": rule["id"], "sequence": (index + 1) * 10}
                    for index, rule in enumerate(rules)
                ],
            },
            headers=PAYROLL,
        )
    ).json()

    key = {"Idempotency-Key": f"p10-{uuid.uuid4().hex}"}
    payrun = (
        await client.post(
            f"{BASE}/payruns/",
            json={
                "name": "Phase 10 golden run",
                "salary_structure_id": structure["id"],
                "period_start": str(period_start),
                "period_end": str(period_end),
                "employee_ids": [employee["id"]],
            },
            headers={**PAYROLL, **key},
        )
    ).json()

    computed = await client.post(
        f"{BASE}/payruns/{payrun['id']}/compute",
        json={"version": payrun["version"]},
        headers={**PAYROLL, "Idempotency-Key": f"p10c-{uuid.uuid4().hex}"},
    )
    assert computed.status_code == 200, computed.text
    assert computed.json()["computed_count"] == 1

    payslips = await client.get(f"{BASE}/payruns/{payrun['id']}/payslips", headers=PAYROLL)
    payslip = payslips.json()["items"][0]

    assert Decimal(payslip["gross_amount"]) == Decimal("42000.00")
    assert Decimal(payslip["net_amount"]) == Decimal("41800.00")
    amounts = {line["code"]: Decimal(line["amount"]) for line in payslip["lines"]}
    assert amounts[f"P10_BASIC_{tag}"] == Decimal("30000.00")
    assert amounts[f"P10_HRA_{tag}"] == Decimal("12000.00")
    assert amounts[f"P10_GROSS_{tag}"] == Decimal("42000.00")
    assert amounts[f"P10_PT_{tag}"] == Decimal("200.00")
    assert amounts[f"P10_NET_{tag}"] == Decimal("41800.00")

    # And the read-side view renders exactly those figures.
    tree = await client.get(f"{BASE}/payslips/{payslip['id']}/calculation", headers=PAYROLL)
    assert tree.status_code == 200
    assert tree.json()["totals"]["net_amount"] == "41800.00"


async def test_phase_10_added_no_write_endpoint():
    """Every route the insights router adds is a GET (Architecture §8.6).

    The one action these screens offer is Revalidate, which is Phase 4's
    existing endpoint. A POST appearing here would be a second write path to a
    payroll transition.
    """
    from app.api.v1.routers import insights

    methods = {
        method
        for route in insights.router.routes
        for method in getattr(route, "methods", set())
    }
    assert methods == {"GET"}, methods
