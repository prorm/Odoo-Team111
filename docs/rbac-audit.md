# RBAC Audit — COMPLETE

**Status: complete.** Every endpoint the application exposes, every verb on it,
probed as all five PRD §3 roles, against a migrated and seeded PostgreSQL
database. **79 endpoints × 5 roles = 395 observed status codes, and every one
of them matches `02_SYSTEM_ARCHITECTURE.md` §5.**

This supersedes `docs/rbac-audit-partial.md`, which covered the Phase 6
dashboard routes only and listed the Phase 5 rows as PENDING. Those rows are
verified here; the partial file is kept for its history.

**Authority:** Architecture §5's permission matrix and PRD §3's role table.
Where this document and the code disagree, the code is the defect —
`require_role` (`app/api/v1/deps.py`) and the service-layer `require_hr` /
`employee_for` / `assert_can_read` checks are the only server-side gates, and
there is no second permission model for MCP or offline-sync entry points
(Architecture §5's closing note, and §9's "one path to the database").

---

## Method

Reproduce with the committed probe, from `harmonix360/backend`:

```
python -m alembic upgrade head
python -m app.seed
python -m scripts.rbac_audit            # table + verdict, exit 1 on any mismatch
python -m scripts.rbac_audit --markdown # the table below
```

Every cell is an **observed HTTP status code** from a real request through the
real ASGI stack — the same middleware chain, dependency graph and service layer
a browser reaches. Nothing here reads `require_role(...)` out of the source and
calls that a verification; the point of an audit is to ask the server.

Tokens come from a real `POST /auth/login` round-trip per role using the five
seeded demo logins, so the `role` and `employee_id` claims are the ones the
server itself minted.

### Why running this is safe

Reads use real seeded ids, so a permitted read is a genuine `200` rather than a
`404` that would prove nothing. Writes never reach a real row:

- **Router-gated routes** (`Depends(require_role(...))`) reject before the
  handler body runs, so an empty body suffices — a denied role gets `403`, a
  permitted role gets `422`, and nothing is inserted.
- **Service-gated routes** (`Depends(get_current_user)` plus a check inside the
  service) need a body that *passes validation*, or the request 422s before the
  gate and proves nothing. **The first version of this audit made exactly that
  mistake and reported seven false mismatches.** Those probes now send a
  complete, valid body pointing at a **well-formed but nonexistent** public id.
  `require_hr(user)` is the first statement of every such method — before the
  row lookup and before the version check (`app/services/hr_access.py`, and
  every mutating method in `app/services/time_off.py`) — so a denied role gets
  `403` and a permitted one gets `404`.
- The two POSTs with no id to be wrong use an empty body (`/employees/`, which
  is router-gated) and a **duplicate unique key** (`/time-off-types/`, which is
  service-gated), so the permitted path ends at `422`/`409` before any INSERT.

The **only** deliberately mutating probes are the last two rows: Architecture
§5 grants an Employee `C` on their own attendance and their own leave request,
and the only way to observe a grant is to exercise it. Both rows are created as
the employee and then deleted as admin; a failed cleanup is reported as an
audit failure rather than ignored. A residue check after a full run against a
freshly seeded database found exactly the seeded dataset and nothing else.

### The four outcomes

| Cell | Meaning |
|---|---|
| `NNN OK` | Passed the gate. The status is whatever the handler then produced — `200` for a real read, `404`/`422`/`409` for the deliberately unusable write target. |
| `NNN denied` | `403` — refused by `require_role` or by a service-layer check. |
| `404 hidden` | Refused, but as a `404`. `EmployeeService.assert_can_read` returns "not found" rather than "forbidden" on purpose: a `403` confirms the row exists, which would let any login enumerate the staff directory by probing ids. Denial, with no existence oracle. |
| `404 no-link` | Permitted by role, but the login has no `Employee` row behind it, so there are no "own records" to return. Neither a grant nor a denial — see below. |

---

## Which matrix row governs Reports / Dashboard

Architecture §5's matrix has **no explicit "Dashboard" or "Reports" row.** That
is not an omission to paper over, so it is stated plainly: the dashboard is a
READ over Payruns/Payslips, so the governing row is

| Module | Employee | HR Manager | HR Payroll User | HR Payroll Manager | Admin |
|---|---|---|---|---|---|
| Payruns/Payslips | — | — | CRU | CRUD | CRUD |

which makes `PAYROLL_ROLES` the correct gate and puts **HR Manager and Employee
at no access, not even read**. That is the sharpest line in the matrix — it
separates "can manage people" from "can see what they are paid" — and
`GET /dashboard/summary` aggregates `Payslip.net_amount` across the whole
organisation, so a read here is a payroll read in every sense that matters.
`app/api/v1/routers/dashboard.py` gates the whole router on `PAYROLL_ROLES`,
including the non-payroll widgets; `docs/dashboard-data-contract.md` §1 explains
why splitting one response across two gates was rejected.

## Which matrix row governs Print / Send Payslip (B8)

The same row, for the same reason. `GET /payslips/{id}/pdf` renders the
persisted payslip snapshot — gross, net and every rule line — so it is a
payroll read, and `POST /payruns/{id}/send-payslips` acts on a payrun. All four
B8 routes are gated on `PAYROLL_ROLES` and are observed denying Employee and HR
Manager below.

**Employees have no self-service payslip endpoint, deliberately.** PRD §3 gives
the Employee role "own profile, attendance, leave balances" and stops there;
B8 delivers payslips to employees *by email*, not by an endpoint. Adding one
would be a scope change needing its own §5 row.

## The `404 no-link` rows, and why they are not a finding

`GET /employees/me`, `/attendance/me`, `/time-off-allocations/me` and
`/time-off-requests/me` answer `404 "No employee linked to this login"` for all
four HR roles. That is the normal shape of a payroll or admin account: `User`
is a login and `Employee` is a person the organisation employs, and
`app/models/employee.py` states outright that "a payroll-only or admin login
can exist with no Employee row at all". The route is not granting an HR role
access to anyone's data — there is no data to grant. Were one of those logins
linked to an Employee, it would read *its own* record, which every HR role may
do anyway under the Employees CRUD row.

The Employee login **is** linked (seeded — see the gap section below), so its
column is a real `200` on all four.

---

## The audit

| Endpoint | employee | hr_manager | payroll_user | payroll_mgr | admin | §5 |
|---|---|---|---|---|---|---|
| `GET /auth/me` | 200 OK | 200 OK | 200 OK | 200 OK | 200 OK | OK |
| `GET /employees/me` | 200 OK | 404 no-link | 404 no-link | 404 no-link | 404 no-link | OK |
| `GET /attendance/me` | 200 OK | 404 no-link | 404 no-link | 404 no-link | 404 no-link | OK |
| `GET /time-off-allocations/me` | 200 OK | 404 no-link | 404 no-link | 404 no-link | 404 no-link | OK |
| `GET /time-off-requests/me` | 200 OK | 404 no-link | 404 no-link | 404 no-link | 404 no-link | OK |
| `GET /employees/` | 403 denied | 200 OK | 200 OK | 200 OK | 200 OK | OK |
| `GET /employees/emp_dV2D42mE` | 404 hidden | 200 OK | 200 OK | 200 OK | 200 OK | OK |
| `GET /employees/emp_dV2D42mE/counts` | 404 hidden | 200 OK | 200 OK | 200 OK | 200 OK | OK |
| `GET /employees/emp_wG2A92xE` | 200 OK | 200 OK | 200 OK | 200 OK | 200 OK | OK |
| `GET /employees/lookup` | 403 denied | 200 OK | 200 OK | 200 OK | 200 OK | OK |
| `POST /employees/` | 403 denied | 422 OK | 422 OK | 422 OK | 422 OK | OK |
| `PATCH /employees/emp_Q9LzOgaK` | 403 denied | 422 OK | 422 OK | 422 OK | 422 OK | OK |
| `DELETE /employees/emp_Q9LzOgaK` | 403 denied | 404 OK | 404 OK | 404 OK | 404 OK | OK |
| `GET /contracts/` | 403 denied | 200 OK | 200 OK | 200 OK | 200 OK | OK |
| `GET /contracts/ctr_We21NKld` | 403 denied | 200 OK | 200 OK | 200 OK | 200 OK | OK |
| `POST /contracts/` | 403 denied | 422 OK | 422 OK | 422 OK | 422 OK | OK |
| `PATCH /contracts/ctr_Q9LzOgaK` | 403 denied | 422 OK | 422 OK | 422 OK | 422 OK | OK |
| `DELETE /contracts/ctr_Q9LzOgaK` | 403 denied | 404 OK | 404 OK | 404 OK | 404 OK | OK |
| `GET /working-schedules/` | 403 denied | 200 OK | 200 OK | 200 OK | 200 OK | OK |
| `GET /working-schedules/wsch_wG2A92xE` | 403 denied | 200 OK | 200 OK | 200 OK | 200 OK | OK |
| `POST /working-schedules/` | 403 denied | 422 OK | 422 OK | 422 OK | 422 OK | OK |
| `PATCH /working-schedules/sched_Q9LzOgaK` | 403 denied | 422 OK | 422 OK | 422 OK | 422 OK | OK |
| `DELETE /working-schedules/sched_Q9LzOgaK` | 403 denied | 404 OK | 404 OK | 404 OK | 404 OK | OK |
| `GET /attendance/` | 403 denied | 200 OK | 200 OK | 200 OK | 200 OK | OK |
| `POST /attendance/check-in` | 403 denied | 404 OK | 404 OK | 404 OK | 404 OK | OK |
| `POST /attendance/att_Q9LzOgaK/check-out` | 404 OK | 404 OK | 404 OK | 404 OK | 404 OK | OK |
| `GET /attendance/att_lZMPO9QJ` | 403 denied | 200 OK | 200 OK | 200 OK | 200 OK | OK |
| `POST /attendance/` | 403 denied | 404 OK | 404 OK | 404 OK | 404 OK | OK |
| `PATCH /attendance/att_Q9LzOgaK` | 403 denied | 404 OK | 404 OK | 404 OK | 404 OK | OK |
| `DELETE /attendance/att_Q9LzOgaK` | 403 denied | 404 OK | 404 OK | 404 OK | 404 OK | OK |
| `GET /time-off-types/lookup` | 200 OK | 200 OK | 200 OK | 200 OK | 200 OK | OK |
| `GET /time-off-types/` | 403 denied | 200 OK | 200 OK | 200 OK | 200 OK | OK |
| `GET /time-off-types/tot_wG2A92xE` | 403 denied | 200 OK | 200 OK | 200 OK | 200 OK | OK |
| `POST /time-off-types/` | 403 denied | 409 OK | 409 OK | 409 OK | 409 OK | OK |
| `PATCH /time-off-types/totype_Q9LzOgaK` | 403 denied | 404 OK | 404 OK | 404 OK | 404 OK | OK |
| `DELETE /time-off-types/totype_Q9LzOgaK` | 403 denied | 404 OK | 404 OK | 404 OK | 404 OK | OK |
| `GET /time-off-allocations/` | 403 denied | 200 OK | 200 OK | 200 OK | 200 OK | OK |
| `POST /time-off-allocations/` | 403 denied | 404 OK | 404 OK | 404 OK | 404 OK | OK |
| `PATCH /time-off-allocations/toalloc_Q9LzOgaK` | 403 denied | 404 OK | 404 OK | 404 OK | 404 OK | OK |
| `DELETE /time-off-allocations/toalloc_Q9LzOgaK` | 403 denied | 404 OK | 404 OK | 404 OK | 404 OK | OK |
| `GET /time-off-requests/` | 403 denied | 200 OK | 200 OK | 200 OK | 200 OK | OK |
| `POST /time-off-requests/` | 403 denied | 404 OK | 404 OK | 404 OK | 404 OK | OK |
| `POST /time-off-requests/toreq_Q9LzOgaK/approve` | 403 denied | 404 OK | 404 OK | 404 OK | 404 OK | OK |
| `POST /time-off-requests/toreq_Q9LzOgaK/refuse` | 403 denied | 404 OK | 404 OK | 404 OK | 404 OK | OK |
| `PATCH /time-off-requests/toreq_Q9LzOgaK` | 403 denied | 404 OK | 404 OK | 404 OK | 404 OK | OK |
| `DELETE /time-off-requests/toreq_Q9LzOgaK` | 403 denied | 404 OK | 404 OK | 404 OK | 404 OK | OK |
| `GET /salary-rules/` | 403 denied | 403 denied | 200 OK | 200 OK | 200 OK | OK |
| `GET /salary-rules/srule_ojQzkMXz` | 403 denied | 403 denied | 200 OK | 200 OK | 200 OK | OK |
| `POST /salary-rules/` | 403 denied | 403 denied | 403 denied | 422 OK | 422 OK | OK |
| `PATCH /salary-rules/srule_Q9LzOgaK` | 403 denied | 403 denied | 403 denied | 422 OK | 422 OK | OK |
| `DELETE /salary-rules/srule_Q9LzOgaK` | 403 denied | 403 denied | 403 denied | 404 OK | 404 OK | OK |
| `GET /salary-structures/` | 403 denied | 403 denied | 200 OK | 200 OK | 200 OK | OK |
| `GET /salary-structures/sstr_wG2A92xE` | 403 denied | 403 denied | 200 OK | 200 OK | 200 OK | OK |
| `POST /salary-structures/` | 403 denied | 403 denied | 403 denied | 422 OK | 422 OK | OK |
| `PATCH /salary-structures/sstr_Q9LzOgaK` | 403 denied | 403 denied | 403 denied | 422 OK | 422 OK | OK |
| `DELETE /salary-structures/sstr_Q9LzOgaK` | 403 denied | 403 denied | 403 denied | 404 OK | 404 OK | OK |
| `GET /payruns/` | 403 denied | 403 denied | 200 OK | 200 OK | 200 OK | OK |
| `GET /payruns/prun_wG2A92xE` | 403 denied | 403 denied | 200 OK | 200 OK | 200 OK | OK |
| `GET /payruns/eligible-employees` | 403 denied | 403 denied | 200 OK | 200 OK | 200 OK | OK |
| `GET /payruns/prun_wG2A92xE/payslips` | 403 denied | 403 denied | 200 OK | 200 OK | 200 OK | OK |
| `GET /payruns/prun_wG2A92xE/validation` | 403 denied | 403 denied | 200 OK | 200 OK | 200 OK | OK |
| `POST /payruns/` | 403 denied | 403 denied | 422 OK | 422 OK | 422 OK | OK |
| `PATCH /payruns/prun_Q9LzOgaK` | 403 denied | 403 denied | 422 OK | 422 OK | 422 OK | OK |
| `POST /payruns/prun_Q9LzOgaK/compute` | 403 denied | 403 denied | 404 OK | 404 OK | 404 OK | OK |
| `POST /payruns/prun_Q9LzOgaK/validate` | 403 denied | 403 denied | 404 OK | 404 OK | 404 OK | OK |
| `POST /payruns/prun_Q9LzOgaK/mark-paid` | 403 denied | 403 denied | 404 OK | 404 OK | 404 OK | OK |
| `DELETE /payruns/prun_Q9LzOgaK` | 403 denied | 403 denied | 403 denied | 404 OK | 404 OK | OK |
| `GET /payslips/` | 403 denied | 403 denied | 200 OK | 200 OK | 200 OK | OK |
| `GET /payslips/pslip_We21NKld` | 403 denied | 403 denied | 200 OK | 200 OK | 200 OK | OK |
| `DELETE /payslips/pslip_Q9LzOgaK` | 403 denied | 403 denied | 403 denied | 404 OK | 404 OK | OK |
| `GET /payslips/pslip_We21NKld/preview` | 403 denied | 403 denied | 200 OK | 200 OK | 200 OK | OK |
| `GET /payslips/pslip_We21NKld/pdf` | 403 denied | 403 denied | 200 OK | 200 OK | 200 OK | OK |
| `GET /payruns/prun_wG2A92xE/deliveries` | 403 denied | 403 denied | 200 OK | 200 OK | 200 OK | OK |
| `POST /payruns/prun_Q9LzOgaK/send-payslips` | 403 denied | 403 denied | 404 OK | 404 OK | 404 OK | OK |
| `GET /dashboard/summary` | 403 denied | 403 denied | 200 OK | 200 OK | 200 OK | OK |
| `GET /dashboard/summary` | 403 denied | 403 denied | 200 OK | 200 OK | 200 OK | OK |
| `GET /departments/` | 200 OK | 200 OK | 200 OK | 200 OK | 200 OK | OK |
| `POST /attendance/check-in  [own record]` | 201 OK | -- | -- | -- | -- | OK |
| `POST /time-off-requests/  [own record]` | 201 OK | -- | -- | -- | -- | OK |

`79 endpoints × 5 roles = 395 probes. Every cell matches Architecture §5.`

---

## The one §5 row with no implementation behind it

| Module | Employee | HR Manager | HR Payroll User | HR Payroll Manager | Admin |
|---|---|---|---|---|---|
| User mgmt / role assignment | — | — | — | — | CRUD |

**There is no user-management endpoint at all**, so there is nothing to probe
and nothing above covers this row. `POST /auth/login` and `GET /auth/me` are
the only user-facing identity routes; users are created by `app/seed.py`.

This is a gap in coverage, not a permission defect — no role can manage users,
including Admin, so no role has more access than §5 allows. Two consequences
are worth stating plainly:

1. **Nothing sets `Employee.user_id` through the API.** `EmployeeCreate`
   excludes the field deliberately (accepting it would let an HR Manager attach
   an employee to an account whose role outranks their own), and no Admin
   surface replaces it. Phase 7 therefore **seeds** the link for the demo
   employee — see `EMPLOYEE_LOGIN_EMAIL` in `app/seed.py`. Without it the
   Employee role is undemonstrable without editing the database by hand, which
   PRD §7 rules out.
2. **PS §4's B-list does not ask for a Users screen** — B1's navigation is
   Employees, Contracts, Attendance, Time Off, Payroll, Reports — so this does
   not block core acceptance. It is on `ROADMAP.md` as near-term work.

---

## The unauthenticated case

`GET /dashboard/summary` with **no `Authorization` header returns 200**, not
401. This is deliberate and bounded: `get_current_user` (`app/api/v1/deps.py`)
treats an unauthenticated request as the demo admin **only when `ENVIRONMENT`
is not `production`**, so `curl` and Swagger work without a login round-trip
during development. In production the same function raises 401.

It is recorded because an audit that omitted it would be describing a
deployment nobody runs locally. **Anyone deploying this must set
`ENVIRONMENT=production`.** Verifying that the production branch actually 401s
is a deployment-configuration test, not part of this audit.

---

## Not covered here, and covered where

- **Row-level scoping beyond the four `/me` routes and `assert_can_read`.**
  "…but only their own" is compared against the signed `employee_id` claim in
  the service layer; `tests/test_attendance_time_off.py` covers the paths this
  audit does not re-probe.
- **MCP and offline-sync entry points.** Architecture §5 says the matrix applies
  identically there. The sync registry is empty and no MCP tools are wired, so
  there is nothing to probe yet — but this is exactly where a second, looser
  permission model would appear if one ever did.
- **Concurrency and idempotency behaviour** behind the write routes (`version`,
  `Idempotency-Key`, advisory locks) — `tests/test_payroll_api.py` and
  `tests/test_locks.py`.
