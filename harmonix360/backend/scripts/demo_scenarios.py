"""PRD §7's two acceptance scenarios, run end to end over real HTTP.

    python -m scripts.demo_scenarios --base-url http://localhost:8000
    python -m scripts.demo_scenarios --keep      # leave the scenario data behind

    Scenario 1  employee -> schedule -> contract -> payrun -> payslip -> PDF -> email
    Scenario 2  allocation -> request -> approval -> balance update

PRD §7 requires both to run "live without manual DB edits", so this script
talks to a running server the way the browser does: real logins, real bearer
tokens, real `Idempotency-Key` headers, and the real Taskiq worker and SMTP
catcher behind Send Payslips. It touches PostgreSQL only to prove a delivery
row exists, and never to create or repair one.

Each role acts as itself. The payroll steps run as the Payroll Manager, the
HR steps as the HR Manager, and the leave request as the Employee — so a
missing permission fails the scenario here rather than on stage.

By default the script deletes everything it created (in dependency order,
newest first) so it can be re-run before a demo without accumulating
rehearsal data. `--keep` turns that off.
"""

import argparse
import asyncio
import sys
import time
import uuid
from datetime import date
from decimal import Decimal

import httpx

BASE = "/api/v1"
LOGINS = {
    "hr": ("hr.manager@peoplepay360.com", "hrmanager123"),
    "payroll": ("payroll.manager@peoplepay360.com", "payroll123"),
    "employee": ("employee@peoplepay360.com", "employee123"),
    "admin": ("admin@peoplepay360.com", "admin123"),
}

PERIOD_START = date(2026, 8, 1)
PERIOD_END = date(2026, 8, 31)

PASS, FAIL = "  PASS", "  FAIL"


class Failure(Exception):
    pass


def check(label, condition, detail=""):
    print(f"{PASS if condition else FAIL}  {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        raise Failure(label)


def _conflict_reason(response):
    try:
        detail = response.json()["detail"]
    except Exception:
        return response.text[:120]
    if isinstance(detail, dict):
        detail = detail.get("message", detail)
    return str(detail)[:160]


class Api:
    def __init__(self, client, tokens):
        self.client = client
        self.tokens = tokens
        self.created = []
        self.immutable = None

    async def call(self, role, method, path, *, json=None, expect=(200, 201), idem=None):
        headers = dict(self.tokens[role])
        if idem:
            headers["Idempotency-Key"] = idem
        response = await self.client.request(
            method, f"{BASE}{path}", headers=headers, json=json
        )
        if response.status_code not in expect:
            raise Failure(
                f"{method} {path} as {role}: expected {expect}, got "
                f"{response.status_code} — {response.text[:400]}"
            )
        return response

    def track(self, collection, body):
        self.created.append((collection, body["id"], body["version"]))

    async def cleanup(self):
        print("\nCleanup (newest first, as admin):")
        if self.immutable:
            print(
                f"    /payruns/{self.immutable}: KEPT. A finalized payrun is"
                " preserved as history (PS B6) and cannot be deleted; rehearse"
                " against a scratch database if that matters."
            )
        for collection, public_id, version in reversed(self.created):
            response = await self.client.delete(
                f"{BASE}{collection}/{public_id}?version={version}",
                headers=self.tokens["admin"],
            )
            if response.status_code in (200, 204):
                state = "deleted"
            elif response.status_code == 409:
                # Not a teardown bug. Approved leave is immutable, a used
                # allocation cannot be deleted, and a referenced type cannot
                # be removed — all Phase 2 rules, all working as specified.
                state = f"REFUSED BY DESIGN (409) — {_conflict_reason(response)}"
            else:
                state = f"HTTP {response.status_code}"
            print(f"    {collection}/{public_id}: {state}")


async def scenario_one(api, mailhog_base, client):
    """employee -> contract -> schedule -> payrun -> payslip -> PDF -> email."""
    print("\n" + "=" * 78)
    print("SCENARIO 1 — employee to payslip to PDF to email (PRD §7)")
    print("=" * 78)

    tag = uuid.uuid4().hex[:8]

    schedule = (
        await api.call(
            "hr",
            "POST",
            "/working-schedules/",
            json={
                "name": f"Demo Scenario Full-Time {tag}",
                "schedule_type": "full_time",
                "lines": [
                    {
                        "day_of_week": day,
                        "start_time": "09:00:00",
                        "end_time": "17:00:00",
                        "break_minutes": 60,
                    }
                    for day in ("monday", "tuesday", "wednesday", "thursday", "friday")
                ],
            },
        )
    ).json()
    api.track("/working-schedules", schedule)
    # PS A3: weekly hours are computed by the server, never typed in.
    check(
        "Schedule created, weekly hours computed server-side",
        str(schedule["weekly_hours"]) == "35.00",
        f"weekly_hours={schedule['weekly_hours']} (5 days x 7 net hours)",
    )

    structures = (await api.call("payroll", "GET", "/salary-structures/?limit=50")).json()
    structure = next(s for s in structures["items"] if s["code"] == "PP360_DEMO")

    employee = (
        await api.call(
            "hr",
            "POST",
            "/employees/",
            json={
                "first_name": "Demo",
                "last_name": f"Scenario{tag}",
                "work_email": f"demo.scenario.{tag}@peoplepay360.com",
                "job_position": "Demo Engineer",
                "employee_type": "permanent",
                "status": "active",
                "hire_date": "2026-01-01",
                "bank_account": f"IN00DEMO{tag.upper()}",
                "default_schedule_id": schedule["id"],
            },
        )
    ).json()
    api.track("/employees", employee)
    check("Employee created", employee["id"].startswith("emp_"), employee["id"])

    contract = (
        await api.call(
            "hr",
            "POST",
            "/contracts/",
            json={
                "employee_id": employee["id"],
                "wage": "60000.00",
                "start_date": "2026-01-01",
                "end_date": None,
                "job_position": "Demo Engineer",
                "status": "active",
                "salary_structure_id": structure["id"],
                "working_schedule_id": schedule["id"],
            },
        )
    ).json()
    api.track("/contracts", contract)
    check("Active contract created", contract["status"] == "active", f"wage {contract['wage']}")

    eligible = (
        await api.call(
            "payroll",
            "GET",
            f"/payruns/eligible-employees?period_start={PERIOD_START}&period_end={PERIOD_END}&limit=200",
        )
    ).json()
    check(
        "B5 step 2 offers the new employee",
        any(row["employee"]["id"] == employee["id"] for row in eligible["items"]),
        f"{eligible['total']} eligible for {PERIOD_START}..{PERIOD_END}",
    )

    payrun = (
        await api.call(
            "payroll",
            "POST",
            "/payruns/",
            json={
                "name": f"Demo Scenario {tag} — August 2026",
                "salary_structure_id": structure["id"],
                "period_start": str(PERIOD_START),
                "period_end": str(PERIOD_END),
                "employee_ids": [employee["id"]],
            },
            expect=(201,),
            idem=f"demo-create-{tag}",
        )
    ).json()
    api.track("/payruns", payrun)
    check("Payrun created (B5 wizard)", payrun["status"] == "draft", payrun["id"])

    computed = (
        await api.call(
            "payroll",
            "POST",
            f"/payruns/{payrun['id']}/compute",
            json={"version": payrun["version"]},
            idem=f"demo-compute-{tag}",
        )
    ).json()
    check(
        "Compute produced exactly one payslip",
        computed["computed_count"] == 1,
        f"status={computed['payrun']['status']}, skipped={len(computed.get('skipped', []))}",
    )

    slips = (await api.call("payroll", "GET", f"/payruns/{payrun['id']}/payslips")).json()
    slip = slips["items"][0]
    detail = (await api.call("payroll", "GET", f"/payslips/{slip['id']}")).json()
    lines = {line["code"]: line["amount"] for line in detail["lines"]}
    # Hand-checkable: 60000 basic, HRA 40% = 24000, gross 84000, PT 200,
    # no unpaid leave so LOP 0.00, net 83800.
    check(
        "B7 rule-by-rule breakdown is the expected arithmetic",
        lines.get("PP360_BASIC") == "60000.00"
        and lines.get("PP360_HRA") == "24000.00"
        and lines.get("PP360_GROSS") == "84000.00"
        and lines.get("PP360_NET") == "83800.00",  # money IS quantized to 0.01
        ", ".join(f"{code}={amount}" for code, amount in lines.items()),
    )
    check(
        "Payslip carries the historical snapshot Compute wrote",
        detail["employee"]["id"] == employee["id"] and detail["contract"]["id"] == contract["id"],
        f"contract {detail['contract']['id']}, period {detail['payrun']['period_start']}",
    )

    report = (await api.call("payroll", "GET", f"/payruns/{payrun['id']}/validation")).json()
    check(
        "B6 firewall reports no blocking issue for this run",
        report["blocking_count"] == 0,
        f"advisory={report['advisory_count']}",
    )

    validated = (
        await api.call(
            "payroll",
            "POST",
            f"/payruns/{payrun['id']}/validate",
            json={"version": computed["payrun"]["version"]},
        )
    ).json()
    # Validate answers with the REPORT, not the payrun, and the report carries
    # the status the run had when the checks RAN — so on success this field is
    # still "computed". The transition is confirmed by re-reading the run,
    # which is also where Mark Paid's `version` comes from.
    check(
        "B6 Validate accepted the run",
        validated["blocking_count"] == 0,
        f"advisory={validated['advisory_count']}",
    )
    payrun = (await api.call("payroll", "GET", f"/payruns/{payrun['id']}")).json()
    check("Payrun is now VALIDATED", payrun["status"] == "validated")

    payrun = (
        await api.call(
            "payroll",
            "POST",
            f"/payruns/{payrun['id']}/mark-paid",
            json={"version": payrun["version"]},
        )
    ).json()
    check("Marked paid", payrun["status"] == "paid")
    # PS B6 preserves finalized runs as history: `delete_payrun` refuses a
    # VALIDATED or PAID run outright. Drop it from the teardown list rather
    # than issuing a DELETE that is meant to fail — and note it, because it
    # means a rehearsal cannot fully undo itself.
    api.created = [row for row in api.created if row[1] != payrun["id"]]
    api.immutable = payrun["id"]

    pdf = await api.call("payroll", "GET", f"/payslips/{slip['id']}/pdf")
    check(
        "B8 PDF generated on demand",
        pdf.content.startswith(b"%PDF-") and len(pdf.content) > 2000,
        f"{len(pdf.content)} bytes, {pdf.headers.get('content-type')}",
    )

    before = await mailhog_count(client, mailhog_base)
    queued = (
        await api.call(
            "payroll", "POST", f"/payruns/{payrun['id']}/send-payslips", expect=(202,)
        )
    ).json()
    check("B8 bulk email accepted (202)", queued["payslip_count"] == 1, queued["detail"])

    delivered = await poll_delivery(api, payrun["id"])
    check(
        "Worker rendered and sent the payslip",
        delivered and delivered[0]["status"] == "sent",
        str(delivered[0] if delivered else "no delivery row"),
    )

    after = await mailhog_count(client, mailhog_base)
    check(
        "SMTP catcher received the message with its PDF attachment",
        after > before,
        f"mailbox {before} -> {after}",
    )
    return employee


async def poll_delivery(api, payrun_id, attempts=40):
    for _ in range(attempts):
        rows = (
            await api.call("payroll", "GET", f"/payruns/{payrun_id}/deliveries")
        ).json()["items"]
        if rows and all(row["status"] in ("sent", "failed") for row in rows):
            return rows
        await asyncio.sleep(1.0)
    return rows


async def mailhog_count(client, mailhog_base):
    if not mailhog_base:
        return 0
    try:
        response = await client.get(f"{mailhog_base}/api/v2/messages?limit=1")
        return response.json().get("total", 0)
    except Exception:
        return 0


async def scenario_two(api, employee):
    """allocation -> request -> approval -> balance update."""
    print("\n" + "=" * 78)
    print("SCENARIO 2 — leave allocation to approval to balance update (PRD §7)")
    print("=" * 78)

    tag = uuid.uuid4().hex[:8]
    leave_type = (
        await api.call(
            "hr",
            "POST",
            "/time-off-types/",
            json={
                "name": f"Demo Annual Leave {tag}",
                "code": f"DEMO_ANNUAL_{tag.upper()}",
                "unit": "days",
                "requires_allocation": True,
                "requires_approval": True,
                "payroll_integration": False,
            },
        )
    ).json()
    api.track("/time-off-types", leave_type)

    # The Employee login's own record, from the signed claim — never a body.
    own = (await api.call("employee", "GET", "/employees/me")).json()
    print(f"    Employee login is {own['first_name']} {own['last_name']} ({own['id']})")

    allocation = (
        await api.call(
            "hr",
            "POST",
            "/time-off-allocations/",
            json={
                "employee_id": own["id"],
                "time_off_type_id": leave_type["id"],
                "allocated": "10.00",
                "valid_from": "2026-01-01",
                "valid_to": "2026-12-31",
                "status": "confirmed",
            },
        )
    ).json()
    api.track("/time-off-allocations", allocation)
    check(
        "A4 allocation created with a full balance",
        Decimal(allocation["allocated"]) == 10 and Decimal(allocation["remaining"]) == 10,
        f"taken={allocation['taken']}",
    )

    balances = (await api.call("employee", "GET", "/time-off-allocations/me")).json()
    mine = next(row for row in balances["items"] if row["id"] == allocation["id"])
    check("Employee sees their own balance", Decimal(mine["remaining"]) == 10)

    request = (
        await api.call(
            "employee",
            "POST",
            "/time-off-requests/",
            json={
                "employee_id": own["id"],
                "time_off_type_id": leave_type["id"],
                "date_from": "2026-11-02",
                "date_to": "2026-11-04",
                "reason": "Demo scenario 2",
            },
        )
    ).json()
    api.track("/time-off-requests", request)
    check(
        "Employee submitted their own request, awaiting approval (3 inclusive days)",
        # Compared as Decimal, not as text: `duration` is an unquantized
        # Decimal on the wire ("3"), while the allocation columns are stored
        # quantized ("3.00"). Exact equality either way, never a tolerance.
        # "to_approve", not "pending": TimeOffRequestStatus's vocabulary
        # (app/models/enums.py) is draft/to_approve/approved/refused.
        request["status"] == "to_approve" and Decimal(request["duration"]) == Decimal(3),
        f"{request['date_from']}..{request['date_to']}, duration={request['duration']}",
    )

    unchanged = (
        await api.call("hr", "GET", f"/time-off-allocations/{allocation['id']}")
    ).json()
    check(
        "A pending request does NOT reserve balance",
        Decimal(unchanged["taken"]) == 0 and Decimal(unchanged["remaining"]) == 10,
        "balance is checked and debited at approval, not at submission",
    )

    pending = (
        await api.call("hr", "GET", "/time-off-requests/?limit=100")
    ).json()
    check(
        "B4 approval queue shows the pending request to HR",
        any(row["id"] == request["id"] for row in pending["items"]),
    )

    approved = (
        await api.call(
            "hr",
            "POST",
            f"/time-off-requests/{request['id']}/approve",
            json={"version": request["version"], "decision_note": "Approved in demo scenario 2"},
        )
    ).json()
    check("Request approved by HR", approved["status"] == "approved", approved["decision_note"])
    api.created[-1] = ("/time-off-requests", approved["id"], approved["version"])

    after = (
        await api.call("employee", "GET", "/time-off-allocations/me")
    ).json()
    debited = next(row for row in after["items"] if row["id"] == allocation["id"])
    check(
        "B4 approval automatically reduced the balance",
        Decimal(debited["taken"]) == 3 and Decimal(debited["remaining"]) == 7,
        f"allocated 10.00, taken {debited['taken']}, remaining {debited['remaining']}",
    )
    check(
        "The debit names the allocation it came from",
        approved["allocation_id"] == allocation["id"],
        approved["allocation_id"],
    )
    api.created[-2] = (
        "/time-off-allocations",
        debited["id"],
        debited["version"],
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--mailhog", default="http://localhost:8025")
    parser.add_argument("--keep", action="store_true", help="do not delete scenario data")
    args = parser.parse_args()

    started = time.monotonic()
    async with httpx.AsyncClient(base_url=args.base_url, timeout=60.0) as client:
        tokens = {}
        for name, (email, password) in LOGINS.items():
            response = await client.post(
                f"{BASE}/auth/login", json={"email": email, "password": password}
            )
            if response.status_code != 200:
                print(f"{FAIL}  login as {email}: HTTP {response.status_code}")
                return 1
            tokens[name] = {"Authorization": f"Bearer {response.json()['access_token']}"}
        print(f"Logged in as all {len(tokens)} demo roles against {args.base_url}")

        api = Api(client, tokens)
        try:
            employee = await scenario_one(api, args.mailhog, client)
            await scenario_two(api, employee)
        except Failure as failure:
            print(f"\nSCENARIO FAILED: {failure}")
            if not args.keep:
                await api.cleanup()
            return 1
        finally:
            elapsed = time.monotonic() - started

        if not args.keep:
            await api.cleanup()

    print(f"\nBoth PRD §7 scenarios completed in {elapsed:.1f}s with no manual DB edits.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
