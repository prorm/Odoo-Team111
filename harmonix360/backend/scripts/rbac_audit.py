"""Full RBAC audit: every API endpoint, every verb, all five roles.

    python -m scripts.rbac_audit            # table + verdict
    python -m scripts.rbac_audit --markdown # the table docs/rbac-audit.md embeds

Every cell is an OBSERVED status code from a real request through the real
ASGI stack — same middleware, same dependency graph, same service layer the
browser reaches. Nothing here reads `require_role(...)` out of the source and
calls that a verification; the point of an audit is to ask the server.

WHY THIS IS SAFE TO RUN AGAINST A REAL DATABASE
-----------------------------------------------
Reads use real seeded ids, so an authorised role produces a real 200 rather
than a 404 that would prove nothing. WRITES DO NOT. Every mutating probe
targets a well-formed but NONEXISTENT public id, and the codebase makes that
sufficient:

  * Router-gated routes (`Depends(require_role(...))`) reject before the
    handler body runs at all.
  * Service-gated routes (`Depends(get_current_user)` plus a service check)
    call `require_hr(user)` as the FIRST statement of the method, before the
    row lookup and before the version check — see app/services/hr_access.py
    and every mutating method in app/services/time_off.py.

So a denied role gets 403 and an allowed role gets 404 (or 422 for a body the
probe deliberately leaves incomplete). Neither path reaches an UPDATE. The one
exception is POST collection creates, which have no id to be wrong; those send
a body referencing nonexistent foreign keys, which fails validation after the
role gate and before any INSERT.

READING THE OUTPUT
------------------
The audit classifies each cell as DENIED (403) or ALLOWED (anything else that
is not 401), and compares that with what `02_SYSTEM_ARCHITECTURE.md` §5
requires. A mismatch is a defect in the code, never a reason to edit the
expectation in this file.
"""

import argparse
import asyncio
import os
import sys

from httpx import ASGITransport, AsyncClient

from app.core.security import encode_public_id
from app.models.enums import UserRole

ROLES = [
    UserRole.EMPLOYEE,
    UserRole.HR_MANAGER,
    UserRole.HR_PAYROLL_USER,
    UserRole.HR_PAYROLL_MANAGER,
    UserRole.ADMIN,
]
SHORT = {
    UserRole.EMPLOYEE: "employee",
    UserRole.HR_MANAGER: "hr_manager",
    UserRole.HR_PAYROLL_USER: "payroll_user",
    UserRole.HR_PAYROLL_MANAGER: "payroll_mgr",
    UserRole.ADMIN: "admin",
}
LOGINS = {
    UserRole.EMPLOYEE: ("employee@peoplepay360.com", "employee123"),
    UserRole.HR_MANAGER: ("hr.manager@peoplepay360.com", "hrmanager123"),
    UserRole.HR_PAYROLL_USER: ("payroll.user@peoplepay360.com", "payroll123"),
    UserRole.HR_PAYROLL_MANAGER: ("payroll.manager@peoplepay360.com", "payroll123"),
    UserRole.ADMIN: ("admin@peoplepay360.com", "admin123"),
}

# Architecture §5's role sets, named the way the matrix names them.
EVERYONE = set(ROLES)
HR_ALL = {
    UserRole.HR_MANAGER,
    UserRole.HR_PAYROLL_USER,
    UserRole.HR_PAYROLL_MANAGER,
    UserRole.ADMIN,
}
PAYROLL = {UserRole.HR_PAYROLL_USER, UserRole.HR_PAYROLL_MANAGER, UserRole.ADMIN}
PAYROLL_ADMIN = {UserRole.HR_PAYROLL_MANAGER, UserRole.ADMIN}
OWN_ONLY = {UserRole.EMPLOYEE}

BASE = "/api/v1"
#: A syntactically valid public id for a row that does not exist. Decodes, so
#: the service reaches its "not found" path rather than a malformed-id 422.
GONE = {
    prefix: encode_public_id(9_999_999, prefix)
    for prefix in ("emp", "ctr", "sched", "att", "totype", "toalloc", "toreq", "srule", "sstr", "prun", "pslip")
}


#: Bodies that pass Pydantic validation so the request actually REACHES the
#: service-layer role check. An empty `{}` returns 422 before the handler runs,
#: which proves nothing about authorisation — the first version of this audit
#: made exactly that mistake and reported seven false mismatches.
def bodies(ids: dict) -> dict:
    when = "2026-08-03T09:00:00+00:00"
    return {
        "attendance_create": {"employee_id": GONE["emp"], "check_in": when},
        "attendance_correct": {
            "version": 1,
            "check_in": when,
            "correction_reason": "RBAC audit probe (never reaches a write)",
        },
        "checkout": {"version": 1},
        # `code` deliberately duplicates the seeded unpaid-leave type. The body
        # is valid, so it reaches `require_hr` (this route is service-gated, so
        # an empty body would 422 before the gate and prove nothing) — and then
        # loses to the uniqueness check with a 409, before any INSERT.
        "type": {
            "name": "RBAC audit probe",
            "code": ids["leave_type_code"],
            "unit": "days",
            "requires_allocation": True,
            "requires_approval": True,
            "payroll_integration": False,
        },
        "allocation_create": {
            "employee_id": GONE["emp"],
            "time_off_type_id": GONE["totype"],
            "allocated": "1.00",
            "valid_from": "2026-01-01",
            "valid_to": "2026-12-31",
            "status": "confirmed",
        },
        "allocation_update": {
            "version": 1,
            "allocated": "1.00",
            "valid_from": "2026-01-01",
            "valid_to": "2026-12-31",
            "status": "confirmed",
        },
        "request_create": {
            "employee_id": GONE["emp"],
            "time_off_type_id": GONE["totype"],
            "date_from": "2026-08-03",
            "date_to": "2026-08-04",
        },
        "decision": {"version": 1},

    }


def probes(ids: dict) -> list[dict]:
    """(method, path, body, expected-allowed-roles, why).

    `expected` is transcribed from Architecture §5, not from the routers. The
    `note` column says which matrix row governs, so a disagreement can be
    argued about in terms of the requirement rather than the implementation.
    """
    b = bodies(ids)
    #: The four HR roles' logins have no Employee row behind them, which is the
    #: normal shape of a payroll or admin account (app/models/employee.py: "a
    #: payroll-only or admin login can exist with no Employee row at all").
    UNLINKED = HR_ALL
    return [
        # ---------------------------------------------- identity (not in §5)
        d("GET", "/auth/me", EVERYONE, "Identity; every authenticated caller"),
        # ------------------------------------------------ own profile row: R
        d("GET", "/employees/me", OWN_ONLY, "§5 row 1 — own profile", no_link=UNLINKED),
        d("GET", "/attendance/me", OWN_ONLY, "§5 row 1 — own attendance", no_link=UNLINKED),
        d("GET", "/time-off-allocations/me", OWN_ONLY, "§5 row 1 — own leave balance", no_link=UNLINKED),
        d("GET", "/time-off-requests/me", OWN_ONLY, "§5 row 1 — own requests", no_link=UNLINKED),
        # ------------------------------------------------------- Employees
        d("GET", "/employees/", HR_ALL, "§5 Employees"),
        d("GET", f"/employees/{ids['other_employee']}", HR_ALL,
          "§5 Employees — someone else's record", hidden=OWN_ONLY),
        d("GET", f"/employees/{ids['other_employee']}/counts", HR_ALL,
          "§5 Employees (B2 smart buttons) — someone else's", hidden=OWN_ONLY),
        # HR and above read this one on their Employees CRUD grant; the
        # Employee reads it because it is theirs. Same 200, two different
        # reasons, which is exactly why the row above (someone else's record)
        # is probed separately.
        d("GET", f"/employees/{ids['own_employee']}", EVERYONE,
          "§5 row 1 — an Employee reading their OWN record"),
        d("GET", "/employees/lookup", HR_ALL, "§5 Employees (manager/report pickers)"),
        # Empty body is correct here and everywhere else a POST uses one: the
        # gate is a ROUTER dependency, so a denied role is rejected before the
        # body is looked at, and an allowed role stops at 422 without inserting.
        d("POST", "/employees/", HR_ALL, "§5 Employees", body={}),
        d("PATCH", f"/employees/{GONE['emp']}", HR_ALL, "§5 Employees", body={"version": 1}),
        d("DELETE", f"/employees/{GONE['emp']}?version=1", HR_ALL, "§5 Employees"),
        # ------------------------------------------------------- Contracts
        d("GET", "/contracts/", HR_ALL, "§5 Contracts"),
        d("GET", f"/contracts/{ids['contract']}", HR_ALL, "§5 Contracts"),
        d("POST", "/contracts/", HR_ALL, "§5 Contracts", body={}),
        d("PATCH", f"/contracts/{GONE['ctr']}", HR_ALL, "§5 Contracts", body={"version": 1}),
        d("DELETE", f"/contracts/{GONE['ctr']}?version=1", HR_ALL, "§5 Contracts"),
        # ------------------------------------------------ Working Schedules
        d("GET", "/working-schedules/", HR_ALL, "§5 Working Schedules"),
        d("GET", f"/working-schedules/{ids['schedule']}", HR_ALL, "§5 Working Schedules"),
        d("POST", "/working-schedules/", HR_ALL, "§5 Working Schedules", body={}),
        d("PATCH", f"/working-schedules/{GONE['sched']}", HR_ALL, "§5 Working Schedules", body={"version": 1}),
        d("DELETE", f"/working-schedules/{GONE['sched']}?version=1", HR_ALL, "§5 Working Schedules"),
        # ------------------------------------------------------- Attendance
        d("GET", "/attendance/", HR_ALL, "§5 Attendance — global collection is HR+"),
        d("POST", "/attendance/check-in", HR_ALL,
          "Checking SOMEONE ELSE in; the own-record grant is verified separately",
          body={"employee_id": GONE["emp"]}),
        d("POST", f"/attendance/{GONE['att']}/check-out", EVERYONE,
          "§5 Attendance (own) — row-scoped inside the service", body=b["checkout"]),
        d("GET", f"/attendance/{ids['attendance']}", HR_ALL, "§5 Attendance — another person's record"),
        d("POST", "/attendance/", HR_ALL,
          "§5 Attendance — creating a record for someone else", body=b["attendance_create"]),
        d("PATCH", f"/attendance/{GONE['att']}", HR_ALL,
          "§5 Attendance — U is the HR correction (B3), never the employee's",
          body=b["attendance_correct"]),
        d("DELETE", f"/attendance/{GONE['att']}?version=1", HR_ALL, "§5 Attendance — D"),
        # ---------------------------------------------------- Time Off types
        d("GET", "/time-off-types/lookup", EVERYONE, "Policy choices an employee request form needs"),
        d("GET", "/time-off-types/", HR_ALL, "§5 — type CONFIGURATION is HR"),
        d("GET", f"/time-off-types/{ids['leave_type']}", HR_ALL, "§5 — type configuration"),
        d("POST", "/time-off-types/", HR_ALL, "§5 — type configuration", body=b["type"]),
        d("PATCH", f"/time-off-types/{GONE['totype']}", HR_ALL, "§5 — type configuration",
          body={**b["type"], "version": 1}),
        d("DELETE", f"/time-off-types/{GONE['totype']}?version=1", HR_ALL, "§5 — type configuration"),
        # ---------------------------------------------- Time Off allocations
        d("GET", "/time-off-allocations/", HR_ALL, "§5 — global collection is HR"),
        d("POST", "/time-off-allocations/", HR_ALL, "§5 — allocating is an HR act",
          body=b["allocation_create"]),
        d("PATCH", f"/time-off-allocations/{GONE['toalloc']}", HR_ALL, "§5 — allocation management",
          body=b["allocation_update"]),
        d("DELETE", f"/time-off-allocations/{GONE['toalloc']}?version=1", HR_ALL, "§5 — allocation management"),
        # ------------------------------------------------- Time Off requests
        d("GET", "/time-off-requests/", HR_ALL, "§5 — global collection is HR"),
        d("POST", "/time-off-requests/", HR_ALL,
          "Requesting leave for SOMEONE ELSE; the own-create grant is verified separately",
          body=b["request_create"]),
        d("POST", f"/time-off-requests/{GONE['toreq']}/approve", HR_ALL,
          "§5 — approve/refuse is HR+, and an Employee never approves their own",
          body=b["decision"]),
        d("POST", f"/time-off-requests/{GONE['toreq']}/refuse", HR_ALL,
          "§5 — approve/refuse is HR+", body=b["decision"]),
        d("PATCH", f"/time-off-requests/{GONE['toreq']}", HR_ALL, "§5 — managing someone's request",
          body={**b["request_create"], "version": 1}),
        d("DELETE", f"/time-off-requests/{GONE['toreq']}?version=1", HR_ALL, "§5 — managing someone's request"),
        # ------------------------------------------ Salary Structures/Rules
        d("GET", "/salary-rules/", PAYROLL, "§5 Salary — Payroll User is R"),
        d("GET", f"/salary-rules/{ids['salary_rule']}", PAYROLL, "§5 Salary — R"),
        d("POST", "/salary-rules/", PAYROLL_ADMIN, "§5 Salary — CRUD is Payroll Manager/Admin", body={}),
        # (empty body is fine here: the gate is a router dependency, not a service call)
        d("PATCH", f"/salary-rules/{GONE['srule']}", PAYROLL_ADMIN, "§5 Salary — authoring", body={"version": 1}),
        d("DELETE", f"/salary-rules/{GONE['srule']}?version=1", PAYROLL_ADMIN, "§5 Salary — authoring"),
        d("GET", "/salary-structures/", PAYROLL, "§5 Salary — R"),
        d("GET", f"/salary-structures/{ids['structure']}", PAYROLL, "§5 Salary — R"),
        d("POST", "/salary-structures/", PAYROLL_ADMIN, "§5 Salary — authoring", body={}),
        d("PATCH", f"/salary-structures/{GONE['sstr']}", PAYROLL_ADMIN, "§5 Salary — authoring", body={"version": 1}),
        d("DELETE", f"/salary-structures/{GONE['sstr']}?version=1", PAYROLL_ADMIN, "§5 Salary — authoring"),
        # -------------------------------------------------- Payruns/Payslips
        d("GET", "/payruns/", PAYROLL, "§5 Payruns/Payslips — R"),
        d("GET", f"/payruns/{ids['payrun']}", PAYROLL, "§5 Payruns/Payslips — R"),
        d("GET", "/payruns/eligible-employees?period_start=2026-08-01&period_end=2026-08-31", PAYROLL, "B5 wizard step 2"),
        d("GET", f"/payruns/{ids['payrun']}/payslips", PAYROLL, "§5 Payruns/Payslips — R"),
        d("GET", f"/payruns/{ids['payrun']}/validation", PAYROLL, "B6 firewall report — R"),
        d("POST", "/payruns/", PAYROLL, "§5 Payruns/Payslips — C", body={}, idempotency=True),
        d("PATCH", f"/payruns/{GONE['prun']}", PAYROLL, "§5 Payruns/Payslips — U", body={"version": 1}),
        d("POST", f"/payruns/{GONE['prun']}/compute", PAYROLL, "B6 Compute — U", body={"version": 1}, idempotency=True),
        d("POST", f"/payruns/{GONE['prun']}/validate", PAYROLL, "B6 Validate — U", body={"version": 1}),
        d("POST", f"/payruns/{GONE['prun']}/mark-paid", PAYROLL, "B6 Mark Paid — U", body={"version": 1}),
        d("DELETE", f"/payruns/{GONE['prun']}?version=1", PAYROLL_ADMIN, "§5 — Payroll User has CRU, NOT D"),
        d("GET", "/payslips/", PAYROLL, "§5 Payruns/Payslips — R"),
        d("GET", f"/payslips/{ids['payslip']}", PAYROLL, "B7 payslip detail — R"),
        d("DELETE", f"/payslips/{GONE['pslip']}?version=1", PAYROLL_ADMIN, "§5 — Payroll User has CRU, NOT D"),
        # -------------------------- B8 documents and delivery (Print / Send)
        d("GET", f"/payslips/{ids['payslip']}/preview", PAYROLL, "B8 Print — payroll read"),
        d("GET", f"/payslips/{ids['payslip']}/pdf", PAYROLL, "B8 Print — payroll read"),
        d("GET", f"/payruns/{ids['payrun']}/deliveries", PAYROLL, "B8 delivery status — payroll read"),
        d("POST", f"/payruns/{GONE['prun']}/send-payslips", PAYROLL, "B8 Send Payslips — payroll act"),
        # ------------------------------------------------- B9 Reports / B1 nav
        d("GET", "/dashboard/summary", PAYROLL, "B9 Reports — aggregates Payslip money, so §5's payroll row"),
        d("GET", "/dashboard/summary?period_start=2026-07-01&period_end=2026-07-31", PAYROLL, "B9 Reports, filtered"),
        d("GET", "/departments/", EVERYONE, "Reference lookup behind employee/contract forms"),
    ]


def d(method, path, expected, note, *, body=None, idempotency=False,
      hidden=frozenset(), no_link=frozenset()):
    """`expected` = roles §5 permits. `hidden` = roles denied via 404 rather
    than 403, which `EmployeeService.assert_can_read` does deliberately so an
    id probe cannot enumerate the staff directory. `no_link` = roles permitted
    by role but with no Employee row behind their login, which the /me routes
    answer with 404 "No employee linked to this login" — an empty result, not
    a grant and not a denial."""
    return {
        "method": method,
        "path": path,
        "expected": expected,
        "note": note,
        "body": body,
        "idempotency": idempotency,
        "hidden": set(hidden),
        "no_link": set(no_link),
    }


def classify(probe, role, code):
    """The observed status, turned into one of four audit outcomes."""
    if code == 403:
        return "DENIED"
    if code == 404 and role in probe["hidden"]:
        return "HIDDEN"
    if code == 404 and role in probe["no_link"]:
        return "NO-LINK"
    if code == 401:
        return "UNAUTH"
    return "ALLOWED"


def required(probe, role):
    if role in probe["hidden"]:
        return "HIDDEN"
    if role in probe["no_link"]:
        return "NO-LINK"
    return "ALLOWED" if role in probe["expected"] else "DENIED"


async def discover(client, headers, employee_headers) -> dict:
    """Real seeded ids, so a permitted read is a 200 rather than a 404."""

    async def first(path, key="items"):
        response = await client.get(f"{BASE}{path}", headers=headers)
        response.raise_for_status()
        rows = response.json()[key]
        if not rows:
            raise SystemExit(
                f"{path} is empty — run `python -m app.seed` against this database first."
            )
        return rows[0]["id"]

    own = (await client.get(f"{BASE}/employees/me", headers=employee_headers)).json()["id"]
    others = (await client.get(f"{BASE}/employees/?limit=50", headers=headers)).json()["items"]
    other = next((row["id"] for row in others if row["id"] != own), None)
    if other is None:
        raise SystemExit("Need at least two employees to probe row-level scoping.")

    leave_types = (
        await client.get(f"{BASE}/time-off-types/?limit=1", headers=headers)
    ).json()["items"]
    payrun = await first("/payruns/?limit=1")
    return {
        "own_employee": own,
        "other_employee": other,
        "employee": await first("/employees/?limit=1"),
        "contract": await first("/contracts/?limit=1"),
        "schedule": await first("/working-schedules/?limit=1"),
        "attendance": await first("/attendance/?limit=1"),
        "leave_type": await first("/time-off-types/?limit=1"),
        "salary_rule": await first("/salary-rules/?limit=1"),
        "structure": await first("/salary-structures/?limit=1"),
        "payrun": payrun,
        "payslip": await first(f"/payruns/{payrun}/payslips?limit=1"),
        "leave_type_code": leave_types[0]["code"],
    }


async def run() -> tuple[list[dict], list[str]]:
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://audit"
    ) as client:
        tokens = {}
        for role in ROLES:
            email, password = LOGINS[role]
            response = await client.post(
                f"{BASE}/auth/login", json={"email": email, "password": password}
            )
            if response.status_code != 200:
                raise SystemExit(
                    f"Could not log in as {email} ({response.status_code}). "
                    "Seed the database first: python -m app.seed"
                )
            tokens[role] = {
                "Authorization": f"Bearer {response.json()['access_token']}"
            }

        ids = await discover(
            client, tokens[UserRole.ADMIN], tokens[UserRole.EMPLOYEE]
        )

        results, failures = [], []
        for probe in probes(ids):
            row = {**probe, "observed": {}, "verdict": {}}
            for role in ROLES:
                headers = dict(tokens[role])
                if probe["idempotency"]:
                    headers["Idempotency-Key"] = f"rbac-audit-{role.value}"
                response = await client.request(
                    probe["method"],
                    f"{BASE}{probe['path']}",
                    headers=headers,
                    json=probe["body"] if probe["body"] is not None else None,
                )
                outcome = classify(probe, role, response.status_code)
                want = required(probe, role)
                row["observed"][role] = response.status_code
                row["verdict"][role] = outcome
                if outcome != want:
                    failures.append(
                        f"{probe['method']} {probe['path']} as {SHORT[role]}: "
                        f"observed {response.status_code} ({outcome}), "
                        f"§5 requires {want}"
                    )
            results.append(row)

        results.extend(await own_record_grants(client, tokens, ids, failures))
        return results, failures


async def own_record_grants(client, tokens, ids, failures) -> list[dict]:
    """§5's two Employee GRANTS: check in, and request leave — for themselves.

    Every other probe in this audit is non-mutating, and these two cannot be:
    "an Employee may create their own attendance" is only demonstrable by
    letting one. So each row is created and then DELETED again as admin, and
    the audit refuses to leave anything behind — if a cleanup fails it is
    reported as a failure rather than shrugged off.
    """
    employee, admin = tokens[UserRole.EMPLOYEE], tokens[UserRole.ADMIN]
    rows = []

    async def grant(label, path, body, note):
        created = await client.post(f"{BASE}{path}", headers=employee, json=body)
        row = {
            "method": "POST",
            "path": f"{path}  [own record]",
            "note": note,
            "expected": OWN_ONLY,
            "hidden": set(),
            "no_link": set(),
            "observed": {r: None for r in ROLES},
            "verdict": {r: "not probed" for r in ROLES},
        }
        row["observed"][UserRole.EMPLOYEE] = created.status_code
        if created.status_code not in (200, 201):
            failures.append(
                f"POST {path} as employee for their OWN record: observed "
                f"{created.status_code}, §5 grants C on own records. {created.text[:160]}"
            )
            row["verdict"][UserRole.EMPLOYEE] = "DENIED"
            return row
        row["verdict"][UserRole.EMPLOYEE] = "ALLOWED"

        body_json = created.json()
        removed = await client.delete(
            f"{BASE}{label}/{body_json['id']}?version={body_json['version']}",
            headers=admin,
        )
        if removed.status_code not in (200, 204):
            failures.append(
                f"Audit cleanup failed: DELETE {label}/{body_json['id']} returned "
                f"{removed.status_code}. A probe row was left in the database."
            )
        return row

    rows.append(
        await grant(
            "/attendance",
            "/attendance/check-in",
            {"employee_id": ids["own_employee"]},
            "§5 Attendance (own) — C. Created then deleted.",
        )
    )
    rows.append(
        await grant(
            "/time-off-requests",
            "/time-off-requests/",
            {
                "employee_id": ids["own_employee"],
                "time_off_type_id": ids["leave_type"],
                "date_from": "2026-11-02",
                "date_to": "2026-11-03",
                "reason": "RBAC audit probe (deleted immediately)",
            },
            "§5 Time Off Requests (own create) — C. Created then deleted.",
        )
    )
    return rows


SYMBOL = {
    "ALLOWED": "OK",
    "DENIED": "denied",
    "HIDDEN": "hidden",
    "NO-LINK": "no-link",
    "UNAUTH": "401",
}


def render(results, markdown: bool) -> str:
    lines = []
    if markdown:
        lines.append(
            "| Endpoint | " + " | ".join(SHORT[r] for r in ROLES) + " | §5 |"
        )
        lines.append("|---|" + "---|" * (len(ROLES) + 1))
    for row in results:
        cells = []
        for role in ROLES:
            code = row["observed"][role]
            outcome = row["verdict"][role]
            text = "--" if code is None else f"{code} {SYMBOL.get(outcome, outcome)}"
            cells.append(text if markdown else f"{text:>9}")
        ok = all(
            row["verdict"][r] in ("not probed", required(row, r)) for r in ROLES
        )
        label = f"{row['method']} {row['path'].split('?')[0]}"
        if markdown:
            lines.append(
                f"| `{label}` | " + " | ".join(cells) + f" | {'OK' if ok else 'MISMATCH'} |"
            )
        else:
            lines.append(f"{label:<62} " + " ".join(cells) + ("" if ok else "  <-- MISMATCH"))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--markdown", action="store_true")
    args = parser.parse_args()

    os.environ.setdefault("ENVIRONMENT", "development")
    results, failures = asyncio.run(run())
    print(render(results, args.markdown))
    print()
    print(f"{len(results)} endpoints x {len(ROLES)} roles = {len(results) * len(ROLES)} probes")
    if failures:
        print(f"\n{len(failures)} MISMATCH(ES) against Architecture §5:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nEvery cell matches Architecture §5.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
