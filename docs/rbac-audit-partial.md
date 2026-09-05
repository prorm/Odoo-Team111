# RBAC Audit — PARTIAL (superseded)

> **SUPERSEDED — do not read this as the current audit.**
>
> Phase 7 produced the complete audit at [rbac-audit.md](rbac-audit.md): every
> endpoint, every verb, all five roles, 395 observed status codes. The
> PENDING PHASE 5 rows below are all verified there.
>
> This file is kept because it records what was actually known at the time,
> and the reasoning about which matrix row governs the dashboard carried
> forward unchanged.

**Status:** partial, on purpose. This document covers the **Payroll Dashboard
(Phase 6)** routes and the payroll reads it aggregates over, verified by real
API calls. Rows that depend on Phase 5 (PS B8 — payslip PDF and bulk email) are
listed and marked **PENDING PHASE 5**; they are not verified, not assumed, and
not silently omitted.

**Authority:** `02_SYSTEM_ARCHITECTURE.md` §5's permission matrix, and PRD §3's
role table. Where this document and the code disagree, the code is the defect —
`require_role` is the single server-side gate (`app/api/v1/deps.py`), and there
is no second permission model for MCP or offline-sync entry points
(Architecture §5, closing note).

> **A note on this file's history.** The task that produced it asked to
> *extend* `docs/rbac-audit-partial.md`. The file did not exist on `origin/dev`
> at commit `cd823a1` — no prior RBAC audit document had been written, and
> `progress.md` never claimed one. It is created here rather than extended, and
> covers only what has actually been verified.

---

## Method

Every cell below is an **observed HTTP status code**, not a reading of the
source. The probe mints a bearer token per role with
`create_access_token(subject=…, role=…)` — the same function `tests/conftest.py`
uses — and issues the request in-process against the ASGI app. Every request is
a GET, so the audit changes nothing it measures.

- Database: `peoplepay360_gap_tests`, migrated to `018_payroll_snapshots` and
  seeded with `python -m app.seed` (including the Loss-of-Pay scenario).
- Code under audit: `origin/dev` at `cd823a17799c7d651066a51053e8db3e296ff3a2`.
- Date of run: 2026-09-05.

Reproduce with the probe described at the end of this document.

---

## Which matrix row governs the dashboard

Architecture §5's matrix has **no explicit "Dashboard" or "Reports" row.** That
is not an omission to be papered over, so it is stated plainly: the dashboard
is a READ over Payruns/Payslips, so the row that governs it is

| Module | Employee | HR Manager | HR Payroll User | HR Payroll Manager | Admin |
|---|---|---|---|---|---|
| Payruns/Payslips | — | — | CRU | CRUD | CRUD |

which makes `PAYROLL_ROLES` (Payroll User, Payroll Manager, Admin) the correct
gate and puts **HR Manager and Employee at no access, not even read**. That is
the sharpest line in the matrix — it is what separates "can manage people" from
"can see what they are paid" — and `GET /dashboard/summary` aggregates
`Payslip.net_amount` across the whole organisation, so a read here is a payroll
read in every sense that matters.

`app/api/v1/routers/dashboard.py` gates the whole router on `PAYROLL_ROLES`,
including the non-payroll widgets (attendance, time off, headcount). The
data-contract doc §1 explains the choice: splitting one response across two
gates would mean a partial dashboard for HR Manager, and the widgets that would
survive are the ones already available on their own screens.

---

## Verified — Payroll Dashboard (Phase 6)

Observed status codes, 2026-09-05:

| Route | employee | hr_manager | hr_payroll_user | hr_payroll_manager | admin | Matches §5? |
|---|---|---|---|---|---|---|
| `GET /api/v1/dashboard/summary` (default period) | 403 | 403 | 200 | 200 | 200 | ✅ |
| `GET /api/v1/dashboard/summary?period_start&period_end` | 403 | 403 | 200 | 200 | 200 | ✅ |
| `GET /api/v1/dashboard/summary?…&employee_type=permanent` | 403 | 403 | 200 | 200 | 200 | ✅ |

The filter variants are probed separately rather than assumed equivalent: a
filter is a query parameter, and a gate applied by the router applies before
any of them are read — but "should" is not "does", and the cheap way to know is
to ask the server.

### The payroll reads the dashboard aggregates over

Included because a dashboard that is correctly gated while its data source is
not would be a false pass.

| Route | employee | hr_manager | hr_payroll_user | hr_payroll_manager | admin | Matches §5? |
|---|---|---|---|---|---|---|
| `GET /api/v1/payruns/` | 403 | 403 | 200 | 200 | 200 | ✅ |
| `GET /api/v1/payslips/` | 403 | 403 | 200 | 200 | 200 | ✅ |
| `GET /api/v1/salary-structures/` | 403 | 403 | 200 | 200 | 200 | ✅ Payroll User is read-only here; writes are `PAYROLL_ADMIN_ROLES` and are covered by `tests/test_salary_api.py`, not re-probed |

### One honest caveat on the unauthenticated case

`GET /api/v1/dashboard/summary` with **no `Authorization` header returns 200**,
not 401. This is deliberate and bounded: `get_current_user`
(`app/api/v1/deps.py`) treats an unauthenticated request as the demo admin
**only when `ENVIRONMENT` is not `production`**, so `curl` and Swagger work
without a login round-trip during development. In production the same function
raises 401.

It is recorded here because an audit that omits it would be describing the
deployment nobody runs locally. **Anyone deploying this must set
`ENVIRONMENT=production`.** Verifying that the production branch actually 401s
is a deployment-configuration test, not covered by this audit.

---

## PENDING PHASE 5 — not verified

Phase 5 (PS B8 — payslip PDF generation and bulk email) has not landed on
`origin/dev`; `origin/phase-5-payslip-pdf-email` exists as a branch but is not
integrated. The routes below either do not exist yet or exist only as an
enqueue boundary. **No status codes are recorded for them, because none have
been observed.**

| Route / action | Expected per §5 | Status |
|---|---|---|
| Print / download payslip PDF (`GET /payslips/{id}/pdf`, or whatever Phase 5 names it) | Payroll User R, Payroll Manager R, Admin R; Employee and HR Manager denied | **PENDING PHASE 5** — route does not exist on `dev` |
| Print payrun (bulk PDF) | as above | **PENDING PHASE 5** — route does not exist on `dev` |
| Send payslips by email — **the worker** | n/a (background task, no HTTP role) | **PENDING PHASE 5** — no worker registered for the `send_payslips` task |
| Employee self-service payslip access | **Deliberately none.** PRD §3 gives Employee "own profile, attendance, leave balances" and stops | **PENDING PHASE 5 decision** — B8 delivers payslips to employees by email, not by an endpoint. If Phase 5 adds one, it is a scope change and needs its own §5 row |

The one Phase-5-adjacent route that **does** exist on `dev` is the enqueue
boundary, and it is gated:

| Route | Gate in code | Status |
|---|---|---|
| `POST /api/v1/payruns/{id}/send-payslips` | `PAYROLL_ROLES` | Exists; enqueues a Taskiq task by name and returns 202. **Not probed in this audit** — it is a POST with side effects (it publishes to the broker), and this audit is read-only by design. Its role gate is covered by `tests/test_payroll_api.py`; its behaviour past the enqueue is Phase 5's |

---

## Not covered by this audit

Stated so the "partial" in the title is specific rather than vague:

- **Write verbs.** Only GETs were probed. Create/update/delete gating across
  employees, contracts, schedules, attendance, time off, salary config and
  payruns is covered by the existing suites (`tests/test_rbac.py`,
  `tests/test_payroll_api.py`, `tests/test_salary_api.py`,
  `tests/test_attendance_time_off.py`) and is not re-verified here.
- **Row-level scoping.** "…but only their own" (Architecture §5's first row) is
  a service-layer rule compared against the signed `employee_id` claim, not a
  role check. `tests/test_attendance_time_off.py` covers it.
- **MCP and offline-sync entry points.** Architecture §5 says the matrix applies
  identically there. The sync registry is empty and no MCP tools are wired, so
  there is nothing to probe yet — but this is exactly where a second, looser
  permission model would appear if one ever did.
- **Phase 7 itself.** This document is Phase 7 *preparation*; the full RBAC
  audit is part of Phase 7 proper.

---

## Reproducing this audit

From `harmonix360/backend`, with `DATABASE_URL` pointing at a migrated and
seeded database and `REDIS_URL` at a local Redis:

```python
# rbac_probe.py — read-only; every request is a GET
import asyncio
from httpx import ASGITransport, AsyncClient
from app.core.security import create_access_token
from app.models.enums import UserRole

ROLES = [UserRole.EMPLOYEE, UserRole.HR_MANAGER, UserRole.HR_PAYROLL_USER,
         UserRole.HR_PAYROLL_MANAGER, UserRole.ADMIN]
ROUTES = ["/api/v1/dashboard/summary",
          "/api/v1/dashboard/summary?period_start=2026-08-01&period_end=2026-08-31",
          "/api/v1/payruns/", "/api/v1/payslips/", "/api/v1/salary-structures/"]

def token(role):
    return {"Authorization": "Bearer " + create_access_token(
        subject=f"{role.value}@peoplepay360.com", role=role.value)}

async def main():
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        for path in ROUTES:
            cells = [str((await c.get(path, headers=token(r))).status_code) for r in ROLES]
            print(f"{path:<70} " + " ".join(cells))

asyncio.run(main())
```

```
PYTHONPATH=. python rbac_probe.py
```

A cell that is not `403` for `employee`/`hr_manager`, or not `200` for the three
payroll roles, is a regression against Architecture §5 — not a test to relax.
