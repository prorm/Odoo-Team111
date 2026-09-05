# PeoplePay360 — Roadmap

The core (Phases 0–7) is **complete and demoable**, and Phase 8 (offline
attendance and leave sync) has landed on top of it. Everything below is the
differentiation layer, which PRD §6 puts in P1–P3 and which is explicitly not
allowed to be built by cutting corners in P0.

> The authoritative record of what is built, and how, is `progress.md`, by
> dated section. Where this file and `progress.md` disagree, `progress.md`
> wins and this file is stale. Every "DONE" below is checkable against a
> `progress.md` section and a commit on `origin/dev`.

Last updated on `phase-8-offline`, which branches from `phase-7-closeout`
(itself `origin/dev` tip `cd823a1` plus `phase-5-payslip-pdf-email` and
`phase-7-prep`). Neither branch is on `origin/dev` yet.

---

## How to read this file

| Marker | Meaning |
|---|---|
| **DONE** | Landed, with a dated `progress.md` section and passing tests |
| **IN FLIGHT** | A branch exists and is not integrated |
| **NOT STARTED** | No code, no branch |

---

## 1. Scope and source of truth

- **Problem statement:** Odoo Hackathon PS — PeoplePay360 HR & Payroll.
- **Requirements:** `01_PRD.md` §4 (A1–A7, B1–B9) — all mandatory.
- **Architecture:** `02_SYSTEM_ARCHITECTURE.md`.
- **Build log:** `progress.md` (dated sections, newest last).
- **RBAC evidence:** `docs/rbac-audit.md` (395 observed status codes).

---

## 2. Delivered — core HR (Phases 0–2)

**Status: DONE.**

### 2.1 Phase 0 — domain skeleton and RBAC

Removed the inherited AssetFlow domain (`Asset`, `TransferRequest`,
`ResourceBooking`, `MeetingRoom`, `Note`) as wrong-domain, and replaced it with
PeoplePay360's fifteen HR entities and PRD §3's five roles, enforced by a single
`require_role` gate. Migration 016 carries the schema. The platform layer (AI,
MCP, sync, realtime, OTel) was left mounted but **dormant** — present, tested,
and connected to nothing — which is what made Phases 8–10 a registration
exercise rather than a rebuild.

### 2.2 Phase 1 — Employee, Working Schedule, Contract

A1/A2/A3. Department-grouped Kanban, list and form; smart-button navigation to
related records. Schedule weekly hours are computed by the server and are not
an input (PS A3). The active-contract non-overlap rule is a Postgres
`EXCLUDE USING gist` constraint, not application validation — so it holds
against concurrent writers and direct SQL alike. Foreign keys are exposed as
nested public ids, never sequential integers.

### 2.3 Phase 2 — Attendance and Time Off

B3/B4 and A4. Check-in/check-out with server-side worked hours and a pure,
clock-injected status derivation; corrections are an HR act, gated at the
service layer with a required reason and before/after audit.

The load-bearing leave decision: **a pending request does not reserve balance.**
Balance is checked and debited at approval, inside the same transaction as the
status change and the audit write, with `version_id_col` on both the allocation
and the request. Approved requests are immutable — cancellation with balance
credit is deliberately future work, not a silent `DELETE`.

---

## 3. Delivered — payroll engine (Phases 3–4, Gap-fix)

**Status: DONE.**

### 3.1 Phase 3 — Salary Structures and Rules

A5/A6. Ordered rule containers; fixed, percentage and `simpleeval` formula
computation over a fixed named context. `resolve_salary_structure` is the one
function Phase 4 calls, and it is deterministic: no AI, no floats, no ambient
state.

### 3.2 Phase 4 — Payrun, Payslip, the deterministic engine

B5/B6/B7. The wizard is two steps and the employee selection is **explicit** —
defaulting it to "everyone active" is the tempting shortcut that silently pays
people nobody chose to pay, most readily in the month somebody joined or left.

State machine: `DRAFT → COMPUTED → VALIDATED → PAID` (`CANCELLED` exists and
nothing sets it — see §11). Compute runs under an advisory lock and requires an
`Idempotency-Key`; a recompute *replaces* payslips rather than adding to them,
and bumps `version` so a stale client is told so. Validate is PRD §5.10's
firewall: it refuses with a 409 whose body **is** the validation report, and
pressing it again does not help — the offending records must be fixed and
Compute re-run.

The golden test computes a payslip through the real HTTP path and asserts it
against arithmetic worked out on paper, with exact `Decimal` equality and no
tolerance anywhere.

### 3.3 Gap-fix — Loss of Pay, and the historical-snapshot bug

`LOP_AMOUNT = (CONTRACT_WAGE / SCHEDULE_WORKING_DAYS_IN_PERIOD) * UNPAID_LEAVE_DAYS`,
all Decimal, quantized to `0.01` `ROUND_HALF_UP` only at the end. A missing or
zero-working-day schedule raises a **blocking** `lop_schedule_unavailable`
rather than substituting a fallback denominator — including when unpaid leave
is zero. There is no defensible invented divisor.

The bug this phase existed to fix: payslips were re-priced from live Contract
rows on read, so a paid payslip changed when someone got a raise. Compute now
persists `reference_snapshot` and `context_snapshot` in the same transaction as
the lines and totals, and every read path — detail, list, calculation, PDF,
email — goes through the shared snapshot serializer.

**The caveat a reader most needs:** migration 018 leaves those columns null on
pre-existing rows, and reads of them return **409 `historical_snapshot_unavailable`**
rather than silently substituting today's contract. That is deliberate.
Finalized history requires historical evidence; it is never backfilled from
mutable live references. Use a freshly migrated and seeded database for demos.

---

## 4. Delivered — reporting (Phase 6)

**Status: DONE.** Note the out-of-order numbering: Phase 6 was built in
parallel with Phase 4 and integrated after it. That is real history and is left
visible rather than renumbered.

### 4.1 Phase 6 — Payroll Dashboard

B9/A7. KPIs, Salary Cost by Department, Monthly Net Salary Trend, operational
alerts, attendance and leave overview, department breakdown — every figure
query-backed, filterable by period, department and employee type. Zero
hardcoded numbers is an acceptance criterion, and it is enforced by tests that
assert hand-computed totals against a hand-designed fixture.

The whole router is gated on `PAYROLL_ROLES`, including the non-payroll
widgets: the dashboard aggregates `Payslip.net_amount` organisation-wide, so a
read here is a payroll read, and **HR Manager has no access to it at all.**
That is the sharpest line in Architecture §5's matrix.

### 4.2 Phase 4/6 integration — warning-shape mismatch

`phase-6-dashboard` forked from `phase-3`, so it was written against a payroll
schema whose *writer* did not yet exist. The dashboard's `_normalize_warning`
guessed at the warning shape, returned only `(category, message)`, and filtered
against a set that predated Phase 4 — dropping `severity` and `references`
entirely and flattening four of Phase 4's six real codes to "other". A payrun
blocked by a missing check-out looked exactly like one blocked by nothing in
particular.

The fix changed only dashboard-side reads; every Phase 4 file was verified
byte-identical afterwards. The lesson is recorded because it generalises: a
parallel branch that reads another branch's data is writing against an
imagined contract until the two are integrated.

---

## 5. Delivered — Payslip PDF and bulk email (Phase 5, PS B8)

**Status: DONE.** Merged into `phase-7-closeout`; `progress.md` carries its
dated section.

### 5.1 What it does

One Jinja template renders the payslip for **preview, PDF download and email
attachment**, so the document a payroll user reads on screen, the file they
download, and the file the employee receives cannot drift apart. WeasyPrint
produces the PDF, with an embedded font and a URL fetcher that refuses every
external resource. `GET /payslips/{id}/preview` also serves computed slips for
the on-screen breakdown; `GET /payslips/{id}/pdf` is restricted to validated or
paid.

Delivery is per-employee and independent. `send_payslips` fans out one
`deliver_payslip` job per payslip, each with its own `PayslipDelivery` row —
so one bad address fails one employee rather than the run. Repeated bulk sends
skip rows already `sent`; pending and failed rows can be queued again.

### 5.2 The constraints it had to honour, and did

- The renderer reads the **stored snapshot** through `payslip_response`, never
  live Contract or Employee rows. A payslip reprinted after a raise shows the
  wage it was computed at.
- Legacy payslips with no `reference_snapshot` surface the 409 rather than
  fabricating a document from live data.
- Warnings are excluded at the data boundary (`model_dump(exclude=...)`), not
  hidden with CSS. A payslip document is not the place to publish an internal
  finding about the person it belongs to.

### 5.3 Honest limits

`sent` means **SMTP accepted the message**, not that it reached an inbox. SMTP
has no exactly-once protocol: a worker crash between acceptance and commit can
duplicate a message on explicit retry. This is stated rather than engineered
around, because the alternative — pretending delivery is transactional — is
worse than the risk.

---

## 6. Delivered — hardening, RBAC audit, demo readiness (Phase 7)

**Status: DONE.** This is the gate the core had to pass before Phase 8+.

### 6.1 Full RBAC audit — all modules, all verbs

`docs/rbac-audit.md`, produced by the committed probe
`harmonix360/backend/scripts/rbac_audit.py`. **79 endpoints × 5 roles = 395
observed status codes**, every one matching Architecture §5, including
Reports/Dashboard and all four B8 Print/Send routes. Every cell is a real
request through the real ASGI stack with a real login token — nothing is
inferred from reading `require_role` in the source.

The audit is non-mutating apart from the two Employee "own record" grants,
which cannot be observed without exercising them; those are created and then
deleted, and a failed cleanup is reported as an audit failure.

Two findings recorded rather than smoothed over: the `404` that
`assert_can_read` returns instead of `403` (deliberate — a `403` confirms the
row exists and would let any login enumerate the staff directory), and the one
matrix row with **no implementation behind it at all**, user management (§11).

### 6.2 Demo dataset completeness

`python -m app.seed` is idempotent and now produces a complete demo
organisation: four departments, five role logins, a six-rule salary structure
including Loss of Pay, a five-person roster across all four departments, and a
**finalized July 2026 payrun** driven through the real `PayrunService` —
create, compute, validate, mark paid — whose payslip is then **rendered to a
real PDF and checked** rather than asserted about.

August 2026 is deliberately left uncomputed: it is the live demo period, and a
pre-computed one would hand every employee a blocking `duplicate_payslip`.

The seed also links the employee login to its Employee row. Nothing sets
`Employee.user_id` through the API (§11), so without that link the Employee
role is undemonstrable without editing the database by hand — which PRD §7
rules out.

### 6.3 Both acceptance scenarios, run live

`scripts/demo_scenarios.py` runs PRD §7's two scenarios end to end over real
HTTP against a running stack, with real logins, real `Idempotency-Key` headers
and the real Taskiq worker and SMTP catcher:

1. employee → schedule → contract → payrun → payslip → **PDF** → **email**,
   with the arithmetic hand-checked at the payslip and the SMTP catcher
   confirmed to have received the message.
2. allocation → request → approval → **balance update**, including the
   assertion that a pending request does *not* reserve balance.

Both pass, with no manual DB edits, in under ten seconds.

### 6.4 Failure modes verified in the browser, not just the API

Driven through the built frontend with Playwright:

- **Duplicate payslip** — computing a second payrun over an already-paid period
  surfaces blocking `duplicate_payslip` findings in the validation panel, and
  **Validate is disabled** while they stand.
- **Overlapping active contract** — refused by the Postgres constraint and
  rendered as an inline form error naming the conflicting contract and its
  period. Not a crash, not a blank screen, not a toast that vanishes.

### 6.5 UI completeness

Every B1–B9 feature has a real screen; no `SectionStub` remains routed. Phase 7
added the one surface the core was missing: **My Profile**, PRD §4's first
Employee user story, reading `/employees/me` so there is no id in the URL to
point at somebody else.

### 6.6 Known limitations disclosed

- **Performance and load characteristics are unmeasured.** No load test, no
  query-plan review, no index audit beyond what the constraints require. The
  dataset is demo-sized. This is a known unknown, not a claim of adequacy.
- `ENVIRONMENT` must be set to `production` on any real deployment;
  development mode treats an unauthenticated request as the demo admin so
  `curl` and Swagger work without a login round-trip.
- The demo database must be freshly migrated and seeded — see §3.3.

---

## 7. Delivered — offline sync (Phase 8, PS §5.3, P1)

**Status: DONE.** `docs/offline-sync-conflicts.md` carries the design notes;
`progress.md` carries the dated section.

### 7.1 What is registered

Exactly two entities, per Architecture §8.3, and both **CREATE-only**:

| Entity | Syncable | Never syncable |
|---|---|---|
| `attendance` | check-in / check-out **creation** | corrections, deletion |
| `time_off_request` | **submission** | approval, refusal, edit, deletion |

Payroll, Salary Rule and Contract are not registered and never will be.
`tests/test_platform_layer.py` asserts the **whole** registered set, not
membership, so adding a third entity has to break a test and be argued for.

### 7.2 What registration actually required

The dormant module predicted that registering an entity would be "one
`register_syncable_entity(...)` call and nothing else". That held for
AssetFlow's `note` and `asset`, which had no domain rules. It did not hold
here, and the difference is the substance of the phase: left to itself the
generic engine would have written rows with **no authorization** (an employee
could post attendance for anyone), **no derivation** (`worked_hours`, `status`
and leave `duration` are server-computed), **served every row to everyone**
(pull filtered by tenant and cursor only), and **accepted UPDATE** — which is
what an attendance correction is, and `AttendanceService.correct` guards it
with `require_hr`.

Each of those is a second, looser path to the database, which §9 forbids. So
`SyncableEntity` gained three optional fields — `allowed_ops`,
`create_handler`, `scope_filter` — each defaulting to the previous behaviour.
The engine's cursor, savepoints, `sync_mutations` idempotency, conflict
envelope, audit write and both HTTP routes are untouched. Creates now run
through the entity's own service method with the authenticated principal: the
same method, the same gate and the same audit row the REST router reaches.

### 7.3 Conflict policy

**The version-conflict path is unreachable for both entities**, by
construction rather than by luck: a CREATE has no `known_version` to disagree
with, and neither entity accepts UPDATE or DELETE. `ConflictModal` therefore
never opens on their account. The machinery is kept because it is what an
UPDATE-capable registration would need, and widening `allowed_ops` is now the
explicit decision that would make it live.

What replaces conflicts for creates is idempotency: `(actor_key,
client_mutation_id)` in `sync_mutations`, with the client id generated when a
mutation is **queued** rather than when it is sent, so every retry of one
queued mutation carries one id. Verified by counting rows in PostgreSQL, and
in the browser across a real kill-network cycle.

### 7.4 Known limits, found by running it

- **No service worker**, so a hard page load or a cold start while offline
  fails. In-app navigation and every already-loaded screen keep working. This
  is an offline-capable data layer, not an installable PWA.
- **A rejected mutation is only logged to the console.** It is not silently
  lost — it leaves the "waiting to sync" list and the server has an audit
  trail — but a queued check-in the server refuses deserves a message.
- **Offline check-ins are timestamped by the device.** Unavoidable, consistent
  with PRD §8, and everything derived from the timestamp is still server-owned.

---

## 8. Phase 9 — AI and MCP (P2)

**Status: NOT STARTED.** The layer is present and dormant. The invariant that
governs the whole phase, from Architecture §7/§10: **AI is architecturally
incapable of writing to `Payslip`/`PayslipLine`.** It may read and narrate; the
deterministic rule engine is the sole author of every figure on a payslip.

### 8.1 Explainability (PRD §5.6)

Narrate the **persisted** rule tree — the payslip's own lines, in sequence,
with their categories and amounts. The AI explains a computation that already
happened; it never produces a figure, and it never recomputes one to check.

### 8.2 MCP tools and their role binding

An MCP session is bound to an authenticated user's role, not to a broad API
key. Each tool wraps the same `require_role`-guarded service method the REST
router calls. The acceptance test is explicit in PRD §7: attempt a mutation
with an unauthorized role and confirm rejection.

### 8.3 What AI must never be allowed to do

Write to a payslip; call a mutating method in `app/services/payroll.py`;
compute money; or act on a user's behalf without confirmation. If an AI answer
would require a number nobody computed deterministically, the correct output is
"unavailable" — never a plausible figure.

---

## 9. Phase 10 — Realtime and observability (P2)

**Status: NOT STARTED.** Presentation over committed database state; REST stays
authoritative.

### 9.1 Channels

Attendance check-ins to the HR dashboard; new time-off requests to approvers;
approval outcomes to the requesting employee; payroll compute and bulk-email
progress to the initiating payroll user. Every broadcast fires **after** the
transaction commits — the socket is a notification of already-true state, never
a store of truth. If the socket layer is down, refetch still produces correct
results, which is why this is P2 and not P0.

### 9.2 Telemetry and error reporting

OTel spans across the payroll compute path, exported to the collector in the
`advanced` compose profile; Sentry for errors. The demo moment PRD §7 names is
a live payroll compute trace visible end to end.

---

## 10. Cross-cutting invariants

These hold across every phase above and are not negotiable per-phase.

1. **Money is `Decimal`/`Numeric(12,2)` end to end**, stringified across every
   JSON boundary. No `float` on the payroll path (Architecture §10).
2. **The deterministic rule engine is the sole author of payslip figures.**
   No second implementation of salary arithmetic, anywhere.
3. **Exactly one active contract per employee per period**, enforced by a
   Postgres EXCLUDE constraint, not by application code.
4. **`require_role` is the single permission gate**, identical for REST, MCP
   and offline sync.
5. **Finalized payruns are history.** Validated and paid runs are never
   recomputed, edited or deleted in place.
6. **Payslips render from their snapshot**, never from live contract data.

---

## 11. Known gaps carried forward

A gap someone decided not to close stays visible here instead of being
forgotten. None of these blocks core acceptance; each is a deliberate omission
with a reason.

| Gap | Why it is open | Where it is discussed |
|---|---|---|
| **No user-management surface at all** | Architecture §5 gives Admin CRUD over users and role assignment, but no endpoint exists — users come from the seed. Not a permission defect (nobody can manage users), but it is the one matrix row with nothing behind it, and it is why `Employee.user_id` has to be seeded. PS §4's B-list asks for no Users screen, so it does not gate acceptance. | `docs/rbac-audit.md` |
| **Approved leave cannot be cancelled** | Cancellation must credit the *originally recorded* allocation transactionally. A generic delete would silently lose the debit. Deliberately deferred rather than half-built. | Phase 2 notes in `progress.md` |
| **Legacy payslips cannot be repaired** | Migration 018 cannot reconstruct a snapshot that was never written. Reads return 409 rather than substituting live data. Fresh database for demos. | §3.3 |
| **`PayrunStatus.CANCELLED` is unreachable** | The enum member exists and no code path sets it. Cancelling a payrun is unspecified work, not a missing line. | Phase 4 notes |
| **Partial-period contracts are a blocking warning, not proration** | Proration is a policy decision nobody has made. The engine refuses rather than inventing a rule. | Architecture §7 |
| **No employee self-service payslip access** | PRD §3 gives Employee "own profile, attendance, leave balances" and stops. B8 delivers payslips by email, not by an endpoint. Adding one is a scope change needing its own §5 row. | `docs/rbac-audit.md` |
| **`WORKED_HOURS` is not a seed input** | `SEED_CONTEXT_NAMES` is a deliberate, agreed set. Adding to it changes what every structure author can reference. | Phase 4 notes |
| **Performance is unmeasured** | No load test, no query-plan review. Demo-sized data only. | §6.6 |
| **No service worker** | Offline works for data, not for a cold start: a hard reload with no network gets the browser's error page. A stale cached shell has its own failure modes, so this is deliberate, not forgotten. | §7.4 |
| **A rejected offline mutation is only logged** | The sync engine drops it from the outbox and writes it to the console instead of telling the person what was refused and why. | `docs/offline-sync-conflicts.md` |
| **Organisation timezone is UTC, hardcoded** | A configurable organisation calendar is real work with real payroll consequences (late/overtime thresholds, period boundaries). Not silently assumed away — stated. | Phase 2 notes |
