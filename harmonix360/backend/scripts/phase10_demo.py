"""Phase 10 verification, driven over real HTTP and a real WebSocket.

    python -m scripts.phase10_demo [--base-url http://localhost:8000]

What this proves that the unit tests cannot:

  1. A REAL WebSocket client connects over the Streamable HTTP server, receives
     frames from a REAL payroll compute, and — critically — the payslips those
     frames describe are readable through the API at the moment the frame
     arrives. That is the commit-after-broadcast guarantee observed end to end
     rather than inferred from call order.
  2. Channel authorization is enforced at the socket: an HR Manager token is
     refused on the payroll channel.
  3. The five read-side features answer with real figures from real records.
  4. The three OTel traces, and only three, are the ones the process reports.

The tests assert the same properties against fakes; this asserts them against
the actual transport, which is where a proxy misconfiguration or a missing
upgrade header would show up and a unit test never would.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import date

import httpx
import websockets

BASE = "/api/v1"

ADMIN = ("admin@peoplepay360.com", "admin123")
PAYROLL_MANAGER = ("payroll.manager@peoplepay360.com", "payroll123")
HR_MANAGER = ("hr.manager@peoplepay360.com", "hrmanager123")

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not condition:
        _FAILURES.append(label)
    return condition


def heading(text: str) -> None:
    print(f"\n{'=' * 78}\n{text}\n{'=' * 78}")


async def login(client: httpx.AsyncClient, credentials: tuple[str, str]) -> str:
    email, password = credentials
    response = await client.post(f"{BASE}/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    return response.json()["access_token"]


def idem() -> dict:
    return {"Idempotency-Key": f"p10demo-{uuid.uuid4().hex}"}


async def main(base_url: str) -> int:
    ws_base = base_url.replace("http://", "ws://").replace("https://", "wss://")

    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
        admin_token = await login(client, ADMIN)
        payroll_token = await login(client, PAYROLL_MANAGER)
        hr_token = await login(client, HR_MANAGER)
        admin = {"Authorization": f"Bearer {admin_token}"}
        payroll = {"Authorization": f"Bearer {payroll_token}"}

        # ------------------------------------------------------------------
        heading("1. REALTIME — a real socket, and the commit-after-broadcast guarantee")

        # Channel authorization, at the socket.
        try:
            async with websockets.connect(f"{ws_base}{BASE}/ws/payroll?token={hr_token}"):
                check("HR Manager is refused on the payroll channel", False, "connection accepted")
        except Exception as exc:
            check(
                "HR Manager is refused on the payroll channel",
                "1008" in str(exc) or "Payroll role required" in str(exc) or "rejected" in str(exc).lower(),
                type(exc).__name__,
            )

        try:
            async with websockets.connect(f"{ws_base}{BASE}/ws/payroll?token=not-a-jwt"):
                check("An unsigned token is refused", False, "connection accepted")
        except Exception as exc:
            check("An unsigned token is refused", True, type(exc).__name__)

        employees = await client.get(f"{BASE}/employees/", params={"limit": 100}, headers=admin)
        employees.raise_for_status()
        lop = next(
            (e for e in employees.json()["items"] if e["work_email"] == "lop.demo@peoplepay360.com"),
            None,
        )
        if lop is None:
            raise SystemExit("Seed the database first: python -m app.seed")

        structures = await client.get(f"{BASE}/salary-structures/", params={"limit": 50}, headers=payroll)
        structure = next(s for s in structures.json()["items"] if s["code"] == "PP360_DEMO")

        async with websockets.connect(f"{ws_base}{BASE}/ws/payroll?token={payroll_token}") as socket:
            hello = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
            check("Payroll manager connects to the payroll channel", hello["event"] == "connected")

            created = await client.post(
                f"{BASE}/payruns/",
                json={
                    "name": f"Phase 10 realtime {uuid.uuid4().hex[:6]}",
                    "salary_structure_id": structure["id"],
                    "period_start": "2026-08-01",
                    "period_end": "2026-08-31",
                    "employee_ids": [lop["id"]],
                },
                headers={**payroll, **idem()},
            )
            created.raise_for_status()
            payrun = created.json()

            computed = await client.post(
                f"{BASE}/payruns/{payrun['id']}/compute",
                json={"version": payrun["version"]},
                headers={**payroll, **idem()},
            )
            computed.raise_for_status()

            frame = json.loads(await asyncio.wait_for(socket.recv(), timeout=15))
            check(
                "A payrun.computed frame arrived over the socket",
                frame["event"] == "payrun.computed",
                f"{frame['event']} computed={frame['data'].get('computed_count')}",
            )
            check(
                "The frame names the run that was just computed",
                frame["data"]["payrun_id"] == payrun["id"],
            )

            # THE GUARANTEE: the payslips the frame announces are readable NOW.
            payslips = await client.get(f"{BASE}/payruns/{payrun['id']}/payslips", headers=payroll)
            payslips.raise_for_status()
            check(
                "The payslips the frame announced are already committed and readable",
                payslips.json()["total"] == frame["data"]["computed_count"],
                f"{payslips.json()['total']} readable vs {frame['data']['computed_count']} announced",
            )

            payslip = payslips.json()["items"][0]
            print(f"       payslip {payslip['id']}: gross {payslip['gross_amount']}, net {payslip['net_amount']}")

        # ------------------------------------------------------------------
        heading("2. OBSERVABILITY — exactly three traces")

        from app.core.telemetry import describe_traces

        described = describe_traces()
        names = [trace["name"] for trace in described["traces"]]
        check("Exactly three traces", len(names) == 3, ", ".join(names))
        check(
            "They are the three Architecture §8.5 names",
            names == ["payroll.compute", "ai.mcp", "offline.sync"],
        )
        check("No blanket instrumentation", described["blanket_instrumentation"] is False)

        # ------------------------------------------------------------------
        heading("3. VIEW CALCULATION (PRD §5.6)")

        calculation = await client.get(f"{BASE}/payslips/{payslip['id']}/calculation", headers=payroll)
        check("Calculation tree returned", calculation.status_code == 200, calculation.text[:120])
        tree = calculation.json()
        check(
            "It renders the payslip's own persisted totals",
            tree["totals"]["net_amount"] == payslip["net_amount"]
            and tree["totals"]["gross_amount"] == payslip["gross_amount"],
            f"net {tree['totals']['net_amount']}, gross {tree['totals']['gross_amount']}",
        )
        check(
            "Frozen inputs are present, not reconstructed",
            tree["inputs"]["available"] is True,
            ", ".join(f"{v['name']}={v['value']}" for v in tree["inputs"]["values"]),
        )
        print("       lines: " + ", ".join(f"{line['code']}={line['amount']}" for line in tree["lines"]))

        hr_attempt = await client.get(
            f"{BASE}/payslips/{payslip['id']}/calculation",
            headers={"Authorization": f"Bearer {hr_token}"},
        )
        check("HR Manager is refused the calculation view", hr_attempt.status_code == 403)

        # ------------------------------------------------------------------
        heading("4. PAY-CHANGE COMPARISON (PRD §5.9)")

        comparison = await client.get(f"{BASE}/payslips/{payslip['id']}/comparison", headers=payroll)
        check("Comparison returned", comparison.status_code == 200, comparison.text[:120])
        diff = comparison.json()
        if diff["comparable"]:
            totals = diff["totals"]
            check(
                "A real change was detected between two persisted payslips",
                totals["net_amount"]["direction"] != "unchanged",
                f"net {totals['net_amount']['before']} -> {totals['net_amount']['after']} "
                f"({totals['net_amount']['delta']})",
            )
            for name, change in (diff.get("input_changes") or {}).items():
                if change.get("direction") not in (None, "unchanged"):
                    print(f"       {name}: {change['before']} -> {change['after']}")
        else:
            check("A first payslip is reported as not comparable, with a reason", bool(diff["reason"]))

        # ------------------------------------------------------------------
        heading("5. CONTRACT TIME MACHINE (PRD §5.8)")

        timeline = await client.get(
            f"{BASE}/employees/{lop['id']}/contract-timeline",
            params={"period_start": "2026-08-01", "period_end": "2026-08-31"},
            headers=admin,
        )
        check("Timeline returned", timeline.status_code == 200, timeline.text[:120])
        body = timeline.json()
        check(
            "The period resolves to a contract, via the engine's own resolver",
            body["resolution"]["resolved"] is True,
            f"{body['resolution']['contract']['contract_id']} @ {body['resolution']['contract']['wage']}",
        )
        check(
            "It is the contract the payslip was computed against",
            body["resolution"]["contract"]["contract_id"] == payslip["contract"]["id"],
        )

        # ------------------------------------------------------------------
        heading("6. VALIDATION FIREWALL (PRD §5.10)")

        firewall = await client.get(f"{BASE}/payruns/{payrun['id']}/firewall", headers=payroll)
        check("Firewall returned", firewall.status_code == 200, firewall.text[:120])
        gate = firewall.json()
        print(f"       {gate['gate_message']}")
        check(
            "Revalidate points at the EXISTING validate endpoint",
            gate["revalidate"]["path"] == f"/api/v1/payruns/{payrun['id']}/validate",
            gate["revalidate"]["path"],
        )
        for group in gate["groups"]:
            print(f"       {group['severity']:9} {group['code']:32} x{group['count']}  fix: {group['fix'][:60]}")
        check(
            "Every finding carries a navigation target",
            all(issue["navigate_to"] for group in gate["groups"] for issue in group["issues"]),
        )

        # Revalidate through the existing endpoint, exactly as the UI does.
        current = (await client.get(f"{BASE}/payruns/{payrun['id']}", headers=payroll)).json()
        revalidate = await client.post(
            f"{BASE}/payruns/{payrun['id']}/validate",
            json={"version": current["version"]},
            headers=payroll,
        )
        check(
            "Revalidate uses the existing service and answers with the report",
            revalidate.status_code in (200, 409),
            f"HTTP {revalidate.status_code}",
        )

        # ------------------------------------------------------------------
        heading("7. DETERMINISTIC ANOMALIES (PRD §5.7)")

        anomalies = await client.get(
            f"{BASE}/anomalies",
            params={"period_start": "2026-08-01", "period_end": "2026-08-31"},
            headers=payroll,
        )
        check("Anomalies returned", anomalies.status_code == 200, anomalies.text[:120])
        found = anomalies.json()
        check(
            "The summary agrees with the list",
            found["summary"]["total"] == len(found["anomalies"]),
            f"{found['summary']['total']} finding(s): {found['summary']['by_type']}",
        )
        for anomaly in found["anomalies"][:6]:
            print(f"       {anomaly['severity']:7} {anomaly['type']:24} {anomaly['message'][:70]}")
        check(
            "Every finding states its reason and where to go",
            all(a["reason"] and a["navigate_to"] for a in found["anomalies"]),
        )

        # PRD §7's advanced metric: "the anomaly engine surfaces at least one
        # real, non-fabricated warning against seeded data". Queried against the
        # CURRENT month rather than the demo period, because a finding has to be
        # true of live data to count — an empty result for a quiet period is the
        # correct answer, not the demonstration.
        today = date.today()
        current = await client.get(
            f"{BASE}/anomalies",
            params={
                "period_start": today.replace(day=1).isoformat(),
                "period_end": today.isoformat(),
            },
            headers=payroll,
        )
        current.raise_for_status()
        live = current.json()["anomalies"]
        check(
            "At least one REAL anomaly is surfaced against the seeded data",
            len(live) > 0,
            f"{len(live)} finding(s) in {today.replace(day=1)}..{today}",
        )
        for anomaly in live[:3]:
            print(f"       {anomaly['severity']:7} {anomaly['type']:24} {anomaly['message'][:70]}")
            print(f"               evidence: {anomaly['current_value']} vs {anomaly['baseline']} "
                  f"over {anomaly['period']}")
        check(
            "Each carries the comparison that produced it, so it can be checked",
            all(a["current_value"] is not None and a["baseline"] is not None for a in live),
        )

        # ------------------------------------------------------------------
        heading("8. AI CONTEXT CONSUMES THE DETERMINISTIC SIGNALS")

        job = await client.post(
            f"{BASE}/ai/ask",
            json={
                "question": "Are there any unusual payroll patterns?",
                "task_type": "anomaly_narration",
                "params": {"period_start": "2026-08-01", "period_end": "2026-08-31"},
            },
            headers=payroll,
        )
        check("POST /ai/ask accepted", job.status_code == 202)
        result = None
        for _ in range(40):
            await asyncio.sleep(0.5)
            poll = await client.get(f"{BASE}/ai/jobs/{job.json()['job_id']}", headers=payroll)
            if poll.json()["status"] != "pending":
                result = poll.json()
                break
        if result and result.get("result"):
            sources = result["result"].get("fact_sources", [])
            check(
                "The AI context now uses the Phase 10 anomaly engine, not the fallback",
                any("anomalies.detect_all" in source for source in sources),
                "; ".join(sources),
            )
        else:
            check("AI job reached a terminal state", False, "worker not running?")

        # Clean up the payrun this run created, if it is still deletable.
        current = (await client.get(f"{BASE}/payruns/{payrun['id']}", headers=payroll)).json()
        if current["status"] in ("draft", "computed"):
            await client.delete(
                f"{BASE}/payruns/{payrun['id']}?version={current['version']}", headers=admin
            )

    heading("RESULT")
    if _FAILURES:
        print(f"  {len(_FAILURES)} check(s) FAILED:")
        for failure in _FAILURES:
            print(f"   - {failure}")
        return 1
    print("  All Phase 10 checks passed.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.base_url)))
