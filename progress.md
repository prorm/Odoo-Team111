# PeoplePay360 progress and handoff

Updated: 2026-09-05. Repository: prorm/Odoo-Team111; working branch: phase-8-offline (Phase 7 gate CLOSED; Phase 8 landed).
Product name is PeoplePay360; source folders retain the Harmonix360 base name.

## Start here

Read 01_PRD.md (especially section 4 and the five-role matrix) and
02_SYSTEM_ARCHITECTURE.md (sections 4–6 and 9–11). The source of truth is the
PeoplePay360 HR/payroll problem statement. Core features are mandatory.
Phases 1–7 precede the optional AI/MCP/offline/realtime work. Do not connect
the dormant platform infrastructure to HR surfaces during core development.
The development-plan file the user quoted is outside this checkout; this
handoff preserves the completed phase and the expected continuation.

## Baseline inherited before this change

Phase 0 removed the wrong-domain AssetFlow models and application paths and
introduced five roles, the 15 HR entities, repositories/services, migration 016,
core/advanced Compose profiles, HR navigation, login, and documentation.
Phase 1 implemented Employee, Working Schedule, and Contract CRUD. Employees
have department-grouped Kanban/list/form and related-record smart buttons.
Schedule hours are calculated by the server. Active contract overlap is a
PostgreSQL EXCLUDE constraint; all activation paths translate conflicts.
Response foreign keys use nested public IDs, never sequential integer IDs.
The inherited backend suite had 191 tests.

AI, MCP, sync, realtime, OTel/Sentry remain dormant. The sync registry contains
no HR entities. The inherited indigo theme is unchanged. No live API keys
were read or modified; .env and Odoo Skills/ remain ignored.

## Phase 2 implemented

Attendance:
- CRUD, POST /attendance/check-in and /attendance/{id}/check-out.
- Employees create/read only their own records; GET /attendance/me is scoped
  by the signed employee_id claim. The global collection is HR Manager+.
- PATCH is an HR correction, with service-level authorization, required
  version and correction reason, corrector reference, before/after audit.
- Checkout accepts only version, uses server time, and refuses repeated checkout.
- worked_hours and status cannot be supplied by clients.
- derive_attendance_status is a pure, clock-injected function. Status priority:
  missing_checkout, absent, overtime, late, present. Existing half_day/corrected
  enum values are retained for compatibility; new writes derive operational
  status and store correction provenance separately.
- UTC is the organization schedule clock. Browser timestamps show local time.
  Worked hours are elapsed time, rounded to hundredths with Decimal HALF_UP;
  schedule breaks affect expected hours but are not silently removed from
  elapsed attendance. No schedule means no invented late/overtime threshold.
  An open record becomes missing_checkout at UTC midnight. Reads rederive
  status without writing; future payroll must use this derivation rather than
  assuming an old stored status is current. No-row absences are represented
  by the pure function, not fabricated daily database records. A zero-length
  recorded interval is absent. A daily absence report is future reporting work.

Time off:
- CRUD for types, allocations, and pending requests; employee self-service
  requests and balances; HR-only global collections and management.
- /time-off-types/lookup exposes policy choices for employee request forms.
  Type configuration collection/detail endpoints remain HR-only.
- Day duration is inclusive calendar days, including weekends. Hours duration
  sums the employee default schedule's net working hours over the date range.
  An hours request without a schedule or without scheduled hours is rejected.
  This phase has date-granular requests; partial-day/hour start/end controls
  are not introduced. No fixed 8-hour conversion is invented.
- Pending requests DO NOT RESERVE BALANCE. Balance is checked at approval.
- Approval selects a confirmed, undeleted allocation for the same employee
  and type that covers the entire request. If multiple qualify, the first
  with enough balance wins, ordered by earliest expiry then ID. No splitting
  across allocations. Validity is relative to the leave dates, so an expired
  calendar period can support a retrospective request if its allocation is
  still confirmed and covers those dates.
- remaining is derived as allocated - taken. Approval increments taken and
  records allocation_id in the SAME transaction/savepoint as request status
  and audit. ORM version_id_col on allocation AND request prevents overspend
  and duplicate approval. Failed audit or stale write rolls everything back.
- Refusal does not look up or mutate allocations.
- requires_allocation=false skips allocation lookup entirely.
- requires_approval=false auto-approves submission through the same debit
  routine; insufficient balance leaves no submitted row.
- All HR roles may approve/refuse; Employee gets 403 at the service layer,
  including callers that bypass HTTP routing.
- Approved requests are immutable. Do not add a generic approved-request
  delete/edit: future cancellation must credit the originally recorded
  allocation transactionally. Used allocations cannot be deleted or have
  their validity changed; allocated cannot fall below taken.
- A type's unit/policy cannot change once referenced, including historical
  references. Shared/exclusive policy locks prevent concurrent first-use
  versus policy edit/delete races. Create a new type for a changed policy.
- PATCH schemas for type, allocation, and request are full editable-form
  replacements with required version, not sparse patches. Identity of an
  existing allocation is immutable; taken/remaining are server-owned.

Frontend:
- Attendance list, employee filter, entry/correction forms, checkout and
  delete confirmation; permission-aware controls and inline failures.
- Time Off Requests / Allocations / Types tabs, management forms,
  approve/refuse notes and confirmation, derived balances, pagination.
- Employee smart buttons now point to ?employee=...&tab=requests|allocations.
- Employees can reach Attendance and Time Off from the main navigation;
  neither screen calls an HR-only employee collection in employee mode.
- Existing palette and UI primitives are reused.

## Important files

Backend:
- app/services/attendance.py: worked hours, pure status, permission-gated operations.
- app/services/time_off.py: policy, duration, CRUD, atomic approval/refusal.
- app/services/hr_access.py: scoped employee references, HR gate, version checks.
- app/schemas/attendance.py and time_off.py: strict inputs and public-ID responses.
- app/api/v1/routers/attendance.py and time_off.py: HTTP adapters.
- alembic/versions/017_attendance_leave_integrity.py: balance/date/time CHECK constraints.
- tests/test_attendance_time_off.py: Phase 2 acceptance and transaction tests.
- tests/__init__.py: prevents dependency-owned tests package shadowing local tests.
- tests/conftest.py: cleanup removes dependent attendance/leave rows before employees.

Frontend:
- src/routes/attendance/AttendancePage.tsx
- src/routes/time-off/TimeOffPage.tsx
- src/routes/hr-shared.tsx
- src/hooks/useAttendanceTimeOff.ts
- src/types/attendance-time-off.ts

## Running and verifying

From repository root:
    docker compose up -d postgres redis

From harmonix360/backend (PowerShell):
    uv sync --extra dev
    $env:DATABASE_URL='postgresql+asyncpg://harmonix360_app:app_password@localhost:5432/peoplepay360'
    $env:MIGRATION_DATABASE_URL='postgresql+asyncpg://postgres:postgres@localhost:5432/peoplepay360'
    $env:REDIS_URL='redis://localhost:6379/0'
    uv run alembic upgrade head
    uv run python -m app.seed
    uv run pytest -q
    uv run uvicorn app.main:app --host 127.0.0.1 --port 8000

From harmonix360/frontend:
    npm ci
    npm run build
    npm run dev -- --host 127.0.0.1 --port 5173

Default Vite/Compose frontend port is 3000; Windows rejected binding that port
in this session, so local UI verification uses 5173. Compose PostgreSQL/Redis
were started from a newly created local volume, then all migrations and seed
ran successfully. Advanced services were not started for this core-only phase.
Tests require REAL PostgreSQL and Redis, and seeded users for login/provenance.

Demo logins (local development only):
- hr.manager@peoplepay360.com / hrmanager123
- employee@peoplepay360.com / employee123
- admin@peoplepay360.com / admin123
- payroll.user@peoplepay360.com / payroll123
- payroll.manager@peoplepay360.com / payroll123

The seed creates logins and departments, not linked employees. An employee
login needs an Employee.user_id relationship before self-service can work.
User-account linking is an Admin concern and is not an Employee CRUD input.
Do not expose user_id in ordinary HR forms to solve demo setup.

## Validation record

- 240 backend tests passed (191 existing + 49 new), real PostgreSQL/Redis.
- Races synchronize two independent sessions after both have read the same
  allocation/version; exactly one commits and the loser stays pending.
- Also tested same-request duplicate approval and failure of the audit write
  after debit, including a caller committing after catching the failure.
- Backend changed-file lint passes with B008 excluded for the repository's
  existing FastAPI Depends default-argument style.
- Frontend TypeScript/Vite production build passes.
- Live browser verification passed using local Playwright Chromium (the in-app
  browser's Node tool was unavailable). Visually inspected screenshots and
  exercised the actual forms: HR login, employee creation, type/allocation
  creation; employee login, own request submission and check-in/out; HR
  approval with balance reaching zero, insufficient-balance inline rejection,
  refusal leaving balance unchanged, and correction recalculating 8.00 hours.
  Employee approval/correction controls were absent. No browser exceptions or
  HTTP 500s occurred during the completed acceptance flows.
- Browser checks caught/fixed dropdown label associations and the inherited
  HR-only navigation entries that hid self-service Attendance and Time Off.
- Local demo fixture retained: phase2.ui@peoplepay360.com (Phase Two
  Verification), Annual UI Leave, one used allocation, one approved and one
  refused request, and corrected attendance. The local Employee.user_id was
  linked to the seeded employee login in PostgreSQL solely for UI verification;
  this fixture is not seeded or committed. Screenshots are machine-local under
  C:/Users/csepr/AppData/Local/Temp/peoplepay-*.png.
- On this OneDrive checkout, broad uvicorn --reload watched .venv files and
  repeatedly restarted. Run without reload, or restrict --reload-dir to app.
- One inherited Redis setex deprecation warning remains; dormant platform
  code is outside this phase.

## Phase 3 implemented

Salary Structures and Rules (PS A5/A6) — deterministic, AI-free config that
Phase 4's payroll engine consumes directly. No `app/ai` import anywhere in
this phase, per Architecture §10.

SalaryRule (A6):
- Full CRUD at `/api/v1/salary-rules/`. Fields: name, unique `code`
  (per-tenant, `uq_salary_rule_code_tenant`), category, sequence,
  `computation_method` (fixed/percentage/formula), and the method-specific
  value: `amount` (fixed: the amount; percentage: e.g. `40.00` for 40%, never
  a `0.40` fraction) + `percentage_base_code`, or `expression` for formula.
  All monetary fields are `Numeric(12,2)`/`Decimal`, never float.
- `code` must be a valid Python identifier — it is the rule engine's
  variable namespace (an `ast.Name` in a formula, a `dict` key in the
  resolver's context).
- A rule's computation-method fields are validated as an exact set at the
  schema level (`app/schemas/salary.py`): a fixed rule may not carry
  `percentage_base_code`/`expression`; a percentage rule needs both `amount`
  and `percentage_base_code`; a formula rule needs `expression` and nothing
  else. A formula's SHAPE (valid arithmetic syntax; no calls, attributes,
  subscripts) is checked here, at rule-save time — independent of any
  structure, since the same rule can sit at different positions in different
  structures.
- Duplicate `code` on create/update is a 409, not a raw integrity 500.

SalaryStructure (A5):
- Full CRUD at `/api/v1/salary-structures/`. `rules` is the ordered
  `SalaryStructureRule` link collection: `{salary_rule_id, sequence}` pairs.
  Sequence lives on the LINK, not only on the rule (see the docstring on
  `SalaryStructureRule` in `app/models/salary.py`) — the same rule can
  legitimately run at different positions in different structures.
  Sending `rules` on PATCH replaces the whole ordered set, same convention as
  `WorkingScheduleUpdate.lines`; omitting it leaves the existing set alone.
- List/Form responses carry server-computed `rule_count` and
  `contract_usage_count` (rows in `contracts.salary_structure_id` pointing at
  this structure) — both computed in one batched query per page, never
  client-submitted, same two-step "validate then set the computed field"
  pattern as `ContractResponse.is_currently_active`.
- **Save-time validation, not payrun-time**: creating or updating a
  structure's `rules` runs every linked rule's `percentage_base_code`/
  `expression` through `validate_structure_rule_order` (see below). A rule
  that references a rule which hasn't run yet in this structure's sequence,
  references itself, or references an unknown name is refused with a 400
  naming the rule and the bad reference — before the structure is ever
  saved. By the time a payrun exists, every saved structure is already
  known-resolvable; Phase 4 does not need to handle this failure mode.

### The structure resolver — the exact function Phase 4 calls

`app/services/salary_resolver.py` is a standalone module with no DB session,
no HTTP, no AI — a hand-built `SalaryStructure`/`SalaryRule` and a plain
`dict` are enough to test it (`tests/test_salary_api.py` does exactly that,
including a hand-computed acceptance test — see below).

```python
from app.services.salary_resolver import resolve_salary_structure, ResolvedRule

def resolve_salary_structure(
    structure: SalaryStructure, seed_context: Mapping[str, Decimal]
) -> list[ResolvedRule]: ...

# ResolvedRule = dataclass(code: str, category: SalaryRuleCategory, amount: Decimal)
```

Phase 4 loads a `SalaryStructure` with `rule_links` populated (it is, by
default — the relationship is `lazy="selectin"`), builds a `seed_context`
dict keyed by the fixed vocabulary in `SEED_CONTEXT_NAMES`
(`WORKED_DAYS`, `CONTRACT_WAGE`, `UNPAID_LEAVE_DAYS` — Architecture §7's
example context; adding a new payroll input is a deliberate change to this
constant, not something a structure author can introduce by typing it into a
formula), and calls `resolve_salary_structure(structure, seed_context)`. The
returned list is in execution order and is exactly what becomes one
`PayslipLine` per entry — `code`/`category`/`amount` map straight across.
Inactive rules (`is_active = False`) are skipped as if absent from the
structure. A lower-level `resolve_ordered_rules(ordered_rules, seed_context)`
is also exported for testing or for any caller that already has a plain
ordered `list[SalaryRule]` rather than a `SalaryStructure` ORM object.

Computation: FIXED and PERCENTAGE are plain `Decimal` arithmetic, quantized
to 2 places `ROUND_HALF_UP`. FORMULA runs through `simpleeval` with an empty
function table (`functions={}`) and a `names` dict limited to exactly the
seed context plus every rule's result computed so far — never Python's
`eval()`, and a custom `_DecimalSafeEval` coerces float literals to `Decimal`
so a formula's own numeric literals can't silently mix types with a named
Decimal input. `FormulaEvaluationError` wraps any runtime failure (a
malformed expression somehow bypassing save-time validation, a divide by
zero); `StructureValidationError` is the save-time-only exception used by
`validate_structure_rule_order` and surfaced by the router as a 400.

Injection defense is two independent layers, both tested directly: (1) a
formula's AST is restricted to bare arithmetic (`_ALLOWED_EXPRESSION_NODES`
— no `Call`/`Attribute`/`Subscript` at all) at rule-save time, so a
`__import__(...)`-shaped formula is refused for having a `Call` node,
regardless of what name is being called; (2) even bypassing that (an ORM
object built directly, not through the schema), `simpleeval`'s own
restricted grammar with `functions={}` refuses to call anything, tested by
invoking `simpleeval.SimpleEval` directly.

RBAC (Architecture §5, the one place read/write splits by role): every GET
uses `PAYROLL_ROLES` (HR Payroll User/Manager, Admin); every mutating route
uses `PAYROLL_ADMIN_ROLES` (HR Payroll Manager, Admin only). HR Manager and
Employee get 403 on every salary-config route, including GET. Both
boundaries are tested — read-only denial of write verbs for HR Payroll User,
and full CRUD for HR Payroll Manager/Admin.

### The acceptance test

`test_hand_computed_structure_matches_resolver_exactly` in
`tests/test_salary_api.py` hand-computes, on paper, a Basic (fixed
30000.00) → HRA (40% of Basic) → Gross (formula: Basic + HRA) → PT (fixed
200.00 deduction) → Net (formula: Gross − PT) structure, and asserts the
resolver reproduces BASIC=30000.00, HRA=12000.00, GROSS=42000.00, PT=200.00,
NET=41800.00 exactly, as `Decimal`, in order, with correct categories. This
exercises every computation method this phase implements composed the way a
real payslip composes them, and is the test that matters most in this phase.

Important files:
- `app/services/salary_resolver.py`: the resolver, `ResolvedRule`,
  `SEED_CONTEXT_NAMES`, `validate_structure_rule_order`,
  `validate_formula_syntax`, both exception types.
- `app/services/salary.py`: `SalaryRuleService`, `SalaryStructureService`
  (create/update/delete, unique-code 409 translation, batched
  `contract_usage_counts`, rule-link replacement).
- `app/schemas/salary.py`: request/response shapes, per-method field
  validation, `code` identifier-shape validation.
- `app/api/v1/routers/salary.py`: full CRUD routes, PAYROLL_ROLES/
  PAYROLL_ADMIN_ROLES split.
- `tests/test_salary_api.py`: resolver unit tests (fixed/percentage/formula,
  forward-reference and unknown-name rejection, both injection-defense
  layers), the hand-computed acceptance test, CRUD, and both RBAC boundaries.
- `tests/conftest.py`: `cleanup_salary_config` fixture (high-water-mark
  cleanup for SalaryRule/SalaryStructure, defensively nulls any Contract
  pointing at a structure being removed).
- No new Alembic migration: migration 016 already created
  `salary_rules`/`salary_structures`/`salary_structure_rules` with every
  column this phase needed (Phase 0 scaffolded the schema ahead of the
  behaviour). `pyproject.toml` adds the one new runtime dependency,
  `simpleeval`.

Validation record: 267 backend tests passed (240 existing + 27 new), real
PostgreSQL/Redis. No regressions in the existing 240.

Environment note for whoever runs this next: on this machine, host port 5432
is already bound by a native Windows PostgreSQL service and 6379 by another
project's Redis container, so `docker-compose.override.yml` republishes this
stack's Postgres/Redis on 5544/6380 — use those ports in `DATABASE_URL`/
`MIGRATION_DATABASE_URL`/`REDIS_URL` on this machine (see that file's own
comment for the exact URLs). Also: `uv run pytest` / `uv run alembic` as
bare console-script invocations resolved to a stale, wrong-project virtualenv
in this session (mixed in `D:\Odoo-Testing\forgeerp\backend\.venv` paths in
tracebacks) even though `uv run python -c "import ..."` resolved correctly;
`uv run python -m pytest` / `uv run python -m alembic` sidestepped it. Worth
re-checking if it recurs.

## Phase 6 implemented (Parallel read-side: Payroll Dashboard & Analytics)

Branch: `phase-6-dashboard` (pushed to `origin/phase-6-dashboard`, commit `772e9d6`).

Built as a parallel read-side analytics layer (PS A7/B9) while avoiding any
conflicts with Phase 4/5 development. Zero payroll calculation logic was
duplicated (reads `Payslip.net_amount` directly), and no Phase 4/5 files were
touched (verified via `git diff --stat` — only `app/main.py`, `frontend/router.tsx`,
and `frontend/package.json` were modified; all other files are new).

### Files created
- `docs/dashboard-data-contract.md` — exact API shape, filter semantics, and
  the definition/formula behind every KPI, chart, and alert.
- `docs/dashboard-phase4-integration.md` — what's assumed about the
  Payrun/Payslip schema and the one integration point (warning shape) Phase 4
  needs to confirm.
- `harmonix360/backend/app/schemas/dashboard.py` — Pydantic response models
  for KPIs, charts, alerts, breakdowns, and overviews.
- `harmonix360/backend/app/services/dashboard.py` — unified analytical query
  service backed by real SQL across Employee, Contract, Attendance, TimeOff,
  and Payslip tables.
- `harmonix360/backend/app/api/v1/routers/dashboard.py` — `GET /api/v1/dashboard/`
  gated by `PAYROLL_ROLES` (Payroll User, Payroll Manager, Admin).
- `harmonix360/backend/tests/test_dashboard.py` — 21 comprehensive tests with
  hand-computed exact Decimal assertions.
- `harmonix360/frontend/src/types/dashboard.ts` — TypeScript interfaces
  matching backend schemas.
- `harmonix360/frontend/src/hooks/useDashboard.ts` — TanStack Query hook with
  filter parameters.
- `harmonix360/frontend/src/routes/reports/ReportsPage.tsx` — production-grade
  analytics UI with Recharts visualizations, date/department filters, KPI cards,
  alert banners, and responsive tab views.

### Files modified
- `harmonix360/backend/app/main.py` — registered dashboard router under `/api/v1`.
- `harmonix360/frontend/src/router.tsx` — routed `/reports` to real `ReportsPage`.
- `harmonix360/frontend/package.json` & `package-lock.json` — added `recharts`.

### Dashboard metrics completed (All A–L requirements)
1. **Total Net Salary Paid:** Sum of `net_amount` for all paid payslips in period.
2. **Payslips Generated:** Count of payslips in period.
3. **Average Salary:** True per-employee average net pay (unique employees, not flat line count).
4. **Approved Time Off:** Total days and count of approved time off requests.
5. **Attendance Health:** Derived from real `WorkingSchedule`/`ScheduleLine` data (actual hours vs expected schedule hours), never hardcoded.
6. **Salary by Department:** Bar chart breakdown of net salary by employee department.
7. **Monthly Trend:** 6-month historical payrun trends (gross, net, deductions).
8. **Payroll Warnings:** Normalized and aggregated alerts across all payslips.
9. **Contract Attention:** Contracts expiring within 30 days + active employees with no active contract.
10. **Attendance Overview:** Real-time headcount (present, late, absent, on leave).
11. **Time-Off Overview:** Current leave breakdown + employee allocation snapshot.
12. **Department Breakdown:** Detailed headcounts, total wages, and leave stats.

### Test & build validation
- **Backend Tests:** 288 passed (267 pre-existing + 21 new dashboard tests) via
  `.venv/Scripts/python.exe -m pytest -q`.
- **Frontend Build:** `tsc -b` clean, `npm run build` succeeds (bundles Recharts cleanly).
- **Live Endpoint Verification:** Verified against live PostgreSQL/Redis:
  - Empty-but-honest aggregates returned on empty database.
  - Strict RBAC: HTTP 403 Forbidden for Employee and HR Manager; HTTP 200 OK
    for Payroll User, Payroll Manager, and Admin.
  - Verified Vite dev server proxy serves `/reports` without console errors.
- **UI Verification Note:** Pixel-level browser screenshots were skipped as no
  browser-automation tool was installed in the local Windows environment.
  Compensated with clean typecheck/build, live API checks through the Vite proxy,
  and comprehensive test suites validating every rendered field.

### Assumptions & Integration Contract for Phase 4
- **"Paid" Definition:** Literal `PayslipStatus.PAID`.
- **Department Grouping:** Uses employee's current department (`Employee.department_id`), not historic contract department.
- **Contract Expiry Horizon:** 30 days.
- **Time-Off Balances:** Snapshot of current remaining allocations (not period-filtered).
- **Remaining Phase 4 Integration Point:** Confirm `Payslip.warnings` structure matches `_normalize_warning`'s recognized keys (`rule_code`, `message`, `severity`), as detailed in `docs/dashboard-phase4-integration.md`.

## Delivery

Phase 3 (`feat(phase3-backend)`) and Phase 6 (`feat(dashboard)`) are committed
and pushed to their respective remote branches:
- Phase 3: `origin/phase-3` (commit `5382648`)
- Phase 6: `origin/phase-6-dashboard` (commit `772e9d6`)

## Next work

Continue deterministic payroll — Payrun/Payslip (Phase 4), the direct
consumer of `resolve_salary_structure` documented above — followed by
reporting/dashboard and core polish (5–7). Re-read the original requirements
for the next phase before implementation. Never use LLMs for payroll
arithmetic; use Decimal and the salary rule engine. Reuse the service-level
permission and transaction paths established here. Do not register HR sync
entities or add AI/MCP integrations until core is ready.

Potential follow-ups must preserve current policies explicitly:
organization timezone/calendar configuration, finer-grained hour requests,
approved-request cancellation with credit, day-by-day absence reporting, and
admin user/employee linking UI. These are not silently implemented assumptions.

## Phase 4 implemented

Payrun and Payslip (PS B5/B6/B7) — the deterministic payroll engine, and the
direct consumer of Phase 3's `resolve_salary_structure`. Working branch:
`phase-4-branch`, cut from `phase-3`.

**Phase 3's arithmetic is not reimplemented anywhere.** `app/services/payroll.py`
imports and calls `resolve_salary_structure(structure, seed_context)`
unmodified; there is no second implementation of fixed/percentage/formula in
this phase, and there must never be one. No `float` is constructed on the
payroll path — every amount is `Numeric(12,2)`/`Decimal`, stringified across
the Taskiq payload boundary.

### Spec note, stated rather than assumed

`03_DEVELOPMENT_PLAN.md` is NOT in this checkout (`find` finds nothing; the
Phase 0-2 handoff above already recorded that "the development-plan file the
user quoted is outside this checkout"). Phase 4 was therefore built from PRD
§4 B5-B7, Architecture §6-§7, and — for implementation details — the CODE:
the Phase 0 model/service/router docstrings in `app/models/payroll.py`,
`app/core/locks.py` and `app/services/payroll.py`, which encode the plan's
intent in-repo. Those Phase 0 docstrings say "Phase 5 adds compute/validate/
mark-paid"; that numbering predates the current phase plan and is stale, not a
different scope.

### The two semantics that were genuinely open

Everything else was already fixed by Phases 1-3. These two were not, so they
are stated here rather than buried in a query:

1. **Which structure runs.** `Payrun.salary_structure_id` is NOT NULL and PRD
   B5 makes structure a wizard step, but Architecture §7 step 2 says "load the
   CONTRACT's SalaryStructure" — and `Contract.salary_structure_id` is
   nullable. Both cannot be authoritative. **Decision: the PAYRUN's structure
   executes**, because `app/models/payroll.py` calls a payrun "one execution of
   one SalaryStructure over one period" and the code is authoritative. A
   contract naming a DIFFERENT structure does not silently redirect the
   computation; it raises an ADVISORY `structure_mismatch` warning so the
   discrepancy is visible. Pinned by
   `test_a_contract_naming_another_structure_is_an_advisory_not_a_silent_swap`.

2. **UNPAID_LEAVE_DAYS with an hours-unit leave type.** Phase 2 explicitly
   refuses to invent an 8-hours-to-a-day conversion. **Decision: count the
   distinct CALENDAR DAYS of (request ∩ period), deduplicated across requests,
   regardless of the type's unit** — the unit governs what an ALLOCATION is
   denominated in, and counting days an absence spans needs no conversion,
   while summing `duration` across a days-type and an hours-type would add two
   different units together. `TimeOffRequest.duration` is not read at all, also
   because a request may straddle the period boundary while its duration covers
   the whole request.

Three further semantics this phase decided, all documented at the top of
`app/services/payroll_context.py`:

- **WORKED_DAYS counts DAYS, not hours**: one distinct UTC date with at least
  one attendance record whose RE-DERIVED status (never the stored column —
  Phase 2's handoff says a stored status can be stale) is neither `absent` nor
  `missing_checkout`. A missing-checkout day is NOT counted (its hours are
  unknown, so asserting either way would be inventing) and raises a BLOCKING
  warning so a human closes it. Hours are deliberately absent: `WORKED_HOURS`
  is not in `SEED_CONTEXT_NAMES`, and adding it is a deliberate change to that
  constant, agreed with whoever authors structures.
- **Attendance and leave are reported side by side, never netted.** A day that
  is both attended and on approved leave counts in both numbers; how to combine
  them is the structure author's formula, not an input this phase hardcodes.
- **Payslip totals**: a structure declaring GROSS/NET rules is taken at its
  word (last one wins). The category-sum fallback exists only because both
  columns are NOT NULL and a structure need not declare either; it never
  overrides a structure that states its own totals.

### The engine

`app/services/payroll_context.py` — Architecture §7 steps 1, 3 and 6 as
session-light, HTTP-free functions: `resolve_period_contract` (the ONE active
contract overlapping the period; uniqueness borrowed entirely from
`contracts_active_period_overlap_excl`, with the unreachable `>1` branch still
written and still raising), `build_payroll_context` (the seed dict, exactly
`SEED_CONTEXT_NAMES`, every value a Decimal), and `warning_checks` (pure, so
the firewall is testable without a payrun in the database).

`app/services/payroll.py` — `PayrunService` owns the transaction, the lock and
the writes; it owns no arithmetic. Compute's ordering is load-bearing and
documented in the method: resolve the public id (smallest possible read) →
`acquire_entity_lock(session, "payrun", id)` → re-read with
`populate_existing` → check version and status → delete this run's payslips
(HARD, so a tombstone cannot occupy `uq_payslip_payrun_employee`) → compute →
write. A recompute calls `flag_modified(payrun, "status")` so `version` moves
even when the status letter does not: replacing every payslip is a material
change, and a client holding the pre-recompute read must be told it is stale.

Warning codes (`Payslip.warnings`, JSONB, `{code, severity, message,
references}`): BLOCKING — `missing_bank_details`, `missing_checkout`,
`contract_gap`, `duplicate_payslip`; ADVISORY — `structure_mismatch`,
`no_attendance`. Validate additionally derives `no_payslip` from persisted
state (selected employees minus employees with a payslip), so a selected
employee with no applicable contract blocks finalization however long ago
Compute ran. Nothing is cached: Revalidate re-reads.

Finality is a one-way ratchet, DRAFT → COMPUTED → VALIDATED → PAID, enforced
in `_assert_recomputable`/`_assert_editable`/`_assert_payslips_mutable` at the
SERVICE layer, so a direct call (MCP tool, script) hits the same wall HTTP
does. There is no "edit this payslip" method by design — a wrong figure is
fixed by correcting its input or its rule and recomputing.

Compute is NOT fatal on a per-employee failure: an employee with no active
contract is reported in the response's `skipped` list and everyone else is
still paid.

### Architecture §6's three obligations

- **Advisory lock** wraps Compute, taken before anything the transaction
  rewrites is read.
- **Idempotency-Key REQUIRED** on payrun create and compute, enforced per-route
  by the new `require_idempotency_key` dependency (the middleware replays a key
  it is given but is silent about a request that omits one, and "required" has
  to live somewhere). Deliberately only on those two operations.
- **`IdempotencyMiddleware` cache key is now scoped to (METHOD, path, key)**
  rather than the key alone. A client generating one key per user action and
  sending it with both "create payrun" and then "compute" — the exact shape of
  the B5 wizard finishing into B6's first action — would otherwise get the
  create's 201 body back as Compute's answer, with no compute having run. The
  middleware is still entity-blind; it just no longer answers one endpoint with
  another's response. `cache_key()` is exported and used by the tests.

### Send Payslips — the enqueue boundary, and nothing past it

`POST /payruns/{id}/send-payslips` validates that the run is PAID, builds a
JSON-safe payload (public ids, Decimals as `str`) and kicks the Taskiq task BY
NAME through `taskiq.kicker.AsyncKicker` — `SEND_PAYSLIPS_TASK_NAME =
"send_payslips"`. Kicked by name on purpose, so this phase neither authors nor
guesses the signature of Phase 5's worker; that name and payload are the
contract between the phases. **No PDF or email code exists in this phase and
no such dependency is imported** — PS B8 is being implemented separately and
concurrently. A broker failure is a 503, never swallowed.

### RBAC

Every route is `PAYROLL_ROLES`; delete (payrun and payslip) is
`PAYROLL_ADMIN_ROLES`, matching PRD §3 (Payroll User has CRU, Payroll Manager
has full CRUD). HR Manager and Employee get 403 on every payroll route
including reads — the sharpest line in the matrix, tested directly. Employees
do NOT read their own payslips here: PRD §3 gives that role "own profile,
attendance, leave balances" and stops; payslip delivery to the employee is
B8's emailed PDF. Adding a self-service endpoint would be a scope decision,
not an oversight to quietly correct.

### Endpoints

`GET/POST /payruns/`, `GET/PATCH/DELETE /payruns/{id}`,
`GET /payruns/eligible-employees` (declared BEFORE `/{public_id}` or it is read
as a payrun id), `POST /payruns/{id}/compute|validate|mark-paid|send-payslips`,
`GET /payruns/{id}/validation` (read-only Revalidate),
`GET /payruns/{id}/payslips`, `GET /payslips/`, `GET/DELETE /payslips/{id}`.
Transitions are POSTs to named sub-resources, never a PATCH that sets `status`:
each runs its own preconditions, and a settable status field is an invitation
to skip them.

### Frontend

`/payroll` (list + PS B5 wizard) and `/payroll/:payrunId` (PS B6 actions, the
§5.10 firewall panel, payslip table, and PS B7's rule-by-rule "View
calculation" dialog). The Payroll section stub is gone from `router.tsx`.
Existing palette and primitives reused; no new design language.

Wizard step 2 offers only ELIGIBLE employees (one active contract covering the
period — the same predicate Compute uses), and nothing is pre-selected: PS B5
calls the selection explicit, and a select-all default is how somebody gets
paid in a run nobody chose to include them in. Exactly one action ever carries
primary emphasis, driven by the run's status, so a paid run does not show
Recompute as the loudest control on the screen.

**Money crosses the wire as a STRING and is never parsed into a JavaScript
number** (`src/types/payroll.ts`): `JSON.parse` on a number would reintroduce,
in the browser, the IEEE imprecision the whole backend exists to avoid.
`formatMoney` groups the integer part by string manipulation only.

### Important files

Backend:
- `app/services/payroll_context.py`: contract resolution, the seed context, the
  §7 step 6 warning checks. Every decided semantic is documented at the top.
- `app/services/payroll.py`: PayrunService (wizard/CRUD, compute under the
  lock, validate, mark paid, enqueue), PayslipService, the immutability wall.
- `app/schemas/payroll.py`: request/response shapes; no client may send
  `worked_days`, `gross_amount`, `net_amount`, `warnings` or any line.
- `app/api/v1/routers/payroll.py`: full routes, RBAC, Idempotency-Key.
- `app/api/v1/deps.py`: `require_idempotency_key`.
- `app/middleware/idempotency.py`: (METHOD, path, key) scoping, `cache_key()`.
- `app/services/attendance.py`: `schedule_expectations` gained an optional
  `schedule=` override so payroll can honour a contract's schedule override
  (PS A3) without a second copy of the status policy. Every Phase 2 caller is
  unchanged.
- `tests/test_payroll_api.py`, `tests/conftest.py` (`cleanup_payroll`; payroll
  rows also cleared defensively in `cleanup_employees`).
- No new Alembic migration: migration 016 already created `payruns`,
  `payrun_employees`, `payslips` and `payslip_lines` with every column this
  phase needed, exactly as it did for Phase 3's salary tables.

Frontend: `src/types/payroll.ts`, `src/hooks/usePayroll.ts`,
`src/routes/payroll/{PayrollPage,PayrunDetailPage,PayrunWizard,PayslipDetail,status}.tsx`.

### Validation record

- **298 backend tests passed** (267 existing + 31 new), real PostgreSQL/Redis,
  no regressions.
- **The golden payslip test** (`test_golden_payslip_is_hand_verifiable_end_to_end`)
  computes a payslip through the REAL HTTP path — real employee, contract,
  attendance, approved leave, structure, and the real Phase 3 resolver — and
  asserts it against arithmetic worked out on paper in the docstring: BASIC
  30000.00 → HRA 12000.00 (40% of BASIC) → GROSS 42000.00 → PT 200.00 → NET
  41800.00, worked_days 3.00. **Exact `Decimal` equality, no tolerance
  anywhere.** A second golden test pins the seed context reaching the rules
  that reference it (PER_DAY/EARNED/DOCKED/NET over WORKED_DAYS and
  UNPAID_LEAVE_DAYS).
- Also tested: duplicate compute (recompute REPLACES, never duplicates, proved
  by changing the data between runs); stale-version compute refused; replayed
  Idempotency-Key returns the first answer AND provably does not re-run the
  engine; Idempotency-Key required on create and compute; two concurrent
  computes (`asyncio.gather`, different keys) — exactly one 200 and one 409,
  one payslip row read past the API; no applicable contract → skipped with a
  reason, `no_payslip` blocks Validate; a draft contract does not make anyone
  payable; overlapping active contracts refused by the constraint and
  resolution still unique afterwards; partial-period contract → `contract_gap`;
  missing bank details + missing checkout; duplicate payslip across two runs
  over one period; structure mismatch advisory; Validate refused then accepted
  after fix-and-recompute (and NOT accepted by pressing Validate again without
  recomputing); advisory warnings do not block; Mark Paid requires Validate
  first; a paid run refuses recompute, edit, delete and payslip delete; Send
  Payslips refused before payment and enqueued after; eligibility list; empty
  selection refused; structure with no active rules refused at Compute; RBAC
  both ways; leave-day de-duplication, period clipping, non-payroll types and
  pending requests all invisible to the context; and `no_float_reaches_a_payslip`
  read back from the database column as a `Decimal`.
- Frontend TypeScript/Vite production build passes.
- **Live browser verification passed** (local Playwright Chromium, headless,
  1440×950). Drove the real UI end to end as HR Payroll Manager: login →
  Payroll → wizard step 1 (structure + period) → step 2 (9 eligible employees
  listed, select all) → Create → Compute → 9 payslips at 42,000.00 gross /
  41,800.00 net / 3.00 worked days → "View calculation" showing Basic 30,000.00,
  Hra 12,000.00, Gross 42,000.00, Pt 200.00, Net 41,800.00 in sequence
  10/20/30/40/50 with categories → Validate → Mark paid → Send payslips
  ("Queued 9 payslip(s) for delivery") → every mutating control disabled
  afterwards. **No page errors and no 5xx responses.**
  A second pass separately observed the firewall blocking a run: 9 blocking
  `duplicate_payslip` findings (each naming the payslip it duplicates) with
  Validate disabled, and a DELETE of the previously paid run refused with 409.
  An earlier pass observed the `missing_bank_details` path the same way.
  Screenshots are machine-local under the session scratchpad.

### Environment notes for whoever runs this next

- On this machine ports **5432/6379 are this stack's Postgres/Redis** (the
  containers `peoplepay360_postgres`/`peoplepay360_redis` publish there
  directly); the 5544/6380 override the Phase 3 note describes was not needed
  in this session.
- `uv run`/`uv sync` FAILED here: OneDrive holds
  `.venv/Lib/site-packages/harmonix360_backend-0.1.0.dist-info` open and uv
  aborts with "Access is denied (os error 5)", which also left `simpleeval`
  (Phase 3's dependency) uninstalled. `uv pip install simpleeval` fixed it, and
  everything in this phase was run with `./.venv/Scripts/python.exe -m pytest`
  / `-m uvicorn` directly, which sidesteps uv's sync step entirely.
- **Port 8000 was already held by an unrelated, older uvicorn** (a Phase 0-era
  build serving GET-only skeleton payroll routes) that this session did not
  start and did not kill. The verification backend therefore ran on 8010 with a
  temporary, uncommitted Vite config proxying to it; that config was deleted
  afterwards. If payroll endpoints 404 in a browser while curl to the backend
  works, check which server the dev proxy is actually reaching before
  suspecting the routes.

### Next work

Phase 5 (PS B8 — payslip PDF + bulk email) consumes the enqueue boundary above:
register a Taskiq task named `send_payslips` taking the single dict payload
`{payrun_id, payrun_name, period_start, period_end, payslips: [{payslip_id,
employee_id, work_email, net_amount, gross_amount}]}` with every amount a
STRING. Then B9's dashboard and core polish (6-7).

Follow-ups that must stay explicit rather than being silently implemented:
employee self-service payslip access (deliberately absent — PRD §3), payslip
explainability narration (PRD §5.6; AI may narrate the persisted tree, never
produce a figure), proration of a partial-period contract (currently a blocking
warning, not an automatic calculation), a `WORKED_HOURS` seed input (a
deliberate change to `SEED_CONTEXT_NAMES`, agreed with structure authors), and
cancelling a payrun (`PayrunStatus.CANCELLED` exists and no path sets it).

### Convergence status (was Phase 6's "Next work" list, now settled)

Phase 6's handoff listed Phase 4 as future work, because `phase-6-dashboard`
was branched from `phase-3` before Phase 4 existed. That list is superseded:

1. ~~Phase 4 (Payroll Engine)~~ — **DONE**, see "Phase 4 implemented" above.
   Note for anyone reading Phase 6's older prose: the real state machine is
   DRAFT → COMPUTED → VALIDATED → PAID. There is no "Approved" status;
   `PayrunStatus` has `draft/computed/validated/paid/cancelled`.
2. **Phase 5 (PS B8):** payslip PDF + bulk email, consuming the `send_payslips`
   enqueue boundary documented above. Still outstanding.
3. **Integration convergence:** Phase 3, 4 and 6 are converged on `dev`; see
   "Phase 6 integrated with Phase 4" below for what that integration actually
   had to fix.

## Phase 6 integrated with Phase 4

`phase-6-dashboard` was branched from `phase-3`, before Phase 4 existed, so it
was written against a payroll schema whose *writer* did not yet exist. This
change rebases it onto Phase 4 and fixes how the dashboard READS what Phase 4
writes. No Phase 4 computation, resolver call, transaction/locking or
warning-generation code was modified — every Phase 4 file is byte-identical to
commit `5e2790a` (verified with `git diff 5e2790a -- <file>`).

### Branch topology, since the handoff above got it wrong

`origin/dev` was still at Phase 2 when this integration started; neither Phase
3 nor Phase 4 had been pushed to it. `phase-6-dashboard`'s merge-base with
`dev` is `074a6d6` (Phase 2) and with `phase-3` is `5382648` — it forked from
**phase-3**, not from a dev containing Phase 4. Rebasing it onto `origin/dev`
as it then stood would have replayed the dashboard onto a Phase 2 base and
stripped Phase 3's resolver out from under it. Phase 4 was pushed and landed on
`dev` first; the rebase target was that.

### The bug this integration existed to find

`_normalize_warning` guessed at three possible key names
(`category` → `type` → `code`) and returned only `(category, message)`. Against
Phase 4's real `Payslip.warnings` entries — `{code, severity, message,
references}`, written by `PayrollWarning.as_dict` — that failed twice:

1. **`severity` and `references` were dropped entirely.** Phase 4's
   blocking/advisory distinction is what PRD §5.10's pre-finalization gate is
   built on, and it never reached the dashboard; neither did the public ids
   that let a user open the offending record.
2. **Four of the six real codes were flattened to `"other"`.** The
   recognized-set filter predated Phase 4 and listed `missing_contract` and
   `contract_attention`, two codes Phase 4 never emits. Only
   `missing_bank_details` and `duplicate_payslip` survived; `missing_checkout`,
   `contract_gap`, `structure_mismatch` and `no_attendance` all rendered as
   "Other". A payrun blocked by a missing check-out looked exactly like one
   blocked by nothing in particular.

The fix reads Phase 4's four keys exactly and passes `code` through
**verbatim** — it is the join key someone uses to find the same warning on the
payslip it came from, so a dashboard-local rename would break precisely the
cross-reference the field exists for. Display text stays in the frontend's
`WARNING_LABELS`. An unknown code is reported with `recognized: false` rather
than relabelled: a code this dashboard has not been taught about is a NEW
Phase 4 warning, and silently renaming it is how a blocking payroll issue
stops being visible.

### A second, unasked-for bug the cross-check turned up

`_period_overlaps_payrun` did not filter `Payrun.deleted_at IS NULL`. Phase 4's
`delete_payrun` SOFT-deletes the run and deliberately leaves its payslips in
place, so every payslip of a deleted draft or computed payrun stayed in
`payslips_generated` and in the warning feed. The money KPIs escaped it only
because they filter `status == PAID` and Phase 4 refuses to delete a validated
or paid run — an accident, not a design, and not one to leave in place for the
next KPI to trip over. Predicate fixed; regression test added.

### The four assumptions, checked against real code

See `docs/dashboard-phase4-integration.md` for the full verdicts. Summary:

| # | Assumption | Verdict |
|---|---|---|
| 1 | `Payslip.net_amount` is authoritative | CONFIRMED |
| 2 | `status == 'paid'` means actually paid | CONFIRMED — and there is no "Approved" status; it is DRAFT → COMPUTED → VALIDATED → PAID |
| 3 | Period comes from the `Payrun` | CORRECTED — the join needed `Payrun.deleted_at IS NULL` |
| 4 | `employee_id`/`contract_id` present; group by employee's department | CONFIRMED |
| 5 | `warnings` shape undefined | CORRECTED — defined now, and the normalizer was wrong |

"Payslips Generated" does **not** double-count a recomputed payrun: Phase 4's
recompute HARD-deletes the run's payslips before rewriting (a soft delete would
leave a tombstone occupying `uq_payslip_payrun_employee`), so one payslip per
employee per run, with the unique constraint as backstop. Two payslips for one
employee in the window means two different payruns over overlapping periods —
which is real, and is itself flagged by Phase 4 as a blocking
`duplicate_payslip` warning.

### Three pre-existing dashboard test failures, fixed

`test_approved_time_off_counts_only_approved_status`,
`test_time_off_overview_breakdown_and_balance` and
`test_employee_type_filter_narrows_every_widget_consistently` were failing
BEFORE this integration (verified by stashing every change and re-running).
They asserted absolute counts against ORG-WIDE aggregates, which hold only on
an empty database; the extra row was `phase2.ui@peoplepay360.com`, the
machine-local Phase 2 browser fixture this file already documents as retained.

The dashboard was right and the tests were wrong: an unfiltered org-wide count
SHOULD count the whole org. The assertions are now scoped by the fixture's own
department, so they are about this dataset instead of about the table. No
product behaviour changed.

### Files changed (dashboard-side reads only)

- `app/services/dashboard.py`: `_normalize_warning` rewritten;
  `_period_overlaps_payrun` gained the deleted-payrun filter; warnings sort
  blocking-first.
- `app/schemas/dashboard.py`: `PayrollWarning` carries
  `code`/`severity`/`references`/`recognized` instead of `category`.
- `tests/test_dashboard.py`: fixture seeds REAL Phase 4-shaped warnings; new
  regression tests for every real code, for severity preservation, for an
  unknown future code, for blocking-first ordering, and for the soft-deleted
  payrun; three pre-existing tests scoped to the fixture's department.
- `frontend/src/types/dashboard.ts`, `frontend/src/routes/reports/ReportsPage.tsx`:
  matching shape, labels for all seven real codes, blocking rendered in the
  harder colour.
- `frontend/src/router.tsx`: rebase conflict resolved keeping BOTH phases'
  routes (`/payroll`, `/payroll/:payrunId`, `/reports`). The now-unused
  `SectionStub` import was removed because `noUnusedLocals` is on and both
  stubs it served are gone; the component file itself is kept.
- `app/api/v1/routers/dashboard.py` was NOT changed, as the integration doc
  predicted.

### Validation record

- **329 backend tests passed** (298 Phase 1-4 + 31 dashboard), real
  PostgreSQL/Redis, no failures and no skips.
- Frontend typecheck + production build pass. `npm install` is required after
  this rebase: Phase 6 added `recharts` to `package.json`, so a `node_modules`
  from before the rebase fails `tsc` with TS2307.

---

## 2026-09-05 — Phase 4 gap fixes and paid-reference snapshot bug

### Starting point and authorization

- Fetched and read `origin/dev:progress.md`, and compared the latest 30 log
  entries and the relevant implementation against its phase claims before
  editing. Base commit: `276ce889339123a4b55e834ccb3b283c1753cabb`, the
  dashboard/warning-shape integration. Phases 1–4 and 6 are integrated;
  Phase 5 PDF/email rendering and the optional platform phases are not.
- The unmodified integration tip passed **329 tests in 51.22 seconds** on
  real PostgreSQL/Redis. Its persisted line amounts and dashboard monetary
  aggregates were correct, but payslip serialization still read LIVE contract
  wage/effective dates and employee details. This contradicted the intended
  historical guarantee. The discrepancy was reported before proceeding;
  the user's subsequent instruction was **“FIX THE BUG and complete it.”**
- Branch: `phase-4-gap-fixes`, created directly from that integration tip.
  The original checkout had unfinished rebase metadata despite reporting a
  clean working tree. It was left alone. Work is in the isolated worktree
  `C:\Users\csepr\peoplepay360-gap-fixes`, not on local dev or main.

### LOP formula and unavailable-input policy

`LOP_AMOUNT = (CONTRACT_WAGE / SCHEDULE_WORKING_DAYS_IN_PERIOD) * UNPAID_LEAVE_DAYS`

`LOP_AMOUNT` is now in the resolver's seed vocabulary. The context builder
counts inclusive period dates whose EXISTING `attendance.schedule_expectations`
returns positive net hours, with the contract schedule override or employee
default. Multiple shifts count as one date; weekends follow the actual
schedule. There is no second implementation of schedule-hour arithmetic.
All inputs and arithmetic are Decimal; the final LOP is quantized to `0.01`
with `ROUND_HALF_UP`, without quantizing the intermediate daily rate.

Missing/deleted schedules, unknown hours, or zero working days produce the
existing `PayrollWarning` shape with code `lop_schedule_unavailable` and
severity `blocking`, even when unpaid leave is zero. `LOP_AMOUNT` is omitted,
not replaced with zero. If an active formula or percentage base consumes LOP,
that employee's computation is skipped and its finding is persisted on the
payrun; Validate reports it through the existing firewall. A structure that
does not consume LOP may still compute its lines, but the same warning blocks
Validate. Fix the schedule and recompute. Attendance's schedule/status logic
and Phase 4's missing-checkout logic were not changed.

The idempotent demo seed now creates `PP360_DEMO` (“PeoplePay360 Demo Salary”):
Basic = contract wage; HRA = 40% Basic; Gross = Basic + HRA; Professional Tax
(demo) = 200; Loss of Pay = `LOP_AMOUNT`; Net = Gross - PT - LOP.
Existing structures/rules are preserved on repeat seed runs. This seeds the
salary configuration, not a complete employee/contract/payrun demo dataset.

New hand-computed golden: March 2025 has 21 Monday–Friday working dates;
contract wage 30,000.00; three approved unpaid days March 10–12.
`(30000 / 21) * 3 = 4285.714285…`, so LOP = **4,285.71**.
Basic 30,000.00 + HRA 12,000.00 = Gross 42,000.00;
Net = 42,000.00 - 200.00 - 4,285.71 = **37,514.29**.
The test uses the actual seeded deduction rule and a contract schedule
override with no employee default. Both previous payroll golden function
sources were compared against origin/dev and are unchanged.

### Gap 2 and Gap 3 verdicts

**Missing checkout: already correct.** One new API regression computes an
otherwise clean run, proves `missing_checkout` is its only blocking finding,
proves Validate returns 409, corrects attendance as HR, proves correction
alone still leaves Validate blocked, then recomputes and validates successfully.

**Historical references: bug fixed.** Compute now captures the public
employee/contract/payrun response references and Decimal-string input context
in the same transaction as the persisted monetary lines and totals. Detail,
list, calculation display, and delivery enqueue use the shared snapshot
serializer. `PayslipLine` remains the source of line amounts; `Payslip` remains
the source of its totals. Status still follows the legitimate
computed → validated → paid lifecycle. Reads never invoke the salary resolver.

One regression pays March, ends its contract March 31, and creates a 36,000.00
contract effective April 1. It ALSO changes the old contract wage/job and the
employee name/email, reproducing the original live-reference failure. The
entire payslip response bytes, shared read/print serialization bytes, list
item, and captured delivery payload remain identical. Queue calls are mocked;
no email is sent. There is no PDF renderer in this integration tip: actual PDF
render/reprint verification belongs to Phase 5, whose worker must consume
`payslip_response` rather than live contract data. Dashboard salary amounts
already use persisted payslip totals; its current department grouping and
contract-attention widget are operational queries, not historical repricing.

**Migration limitation:** migration `018_payroll_snapshots` adds nullable
`Payslip.reference_snapshot`, `Payslip.context_snapshot`, and
`Payrun.computation_warnings`. It does not guess historical wages or rewrite
existing lines/totals. Legacy rows without reference snapshots return 409
`historical_snapshot_unavailable` on detail/list/delivery reads. A list page
containing such a row also fails clearly; it does not silently hide the row.
Draft/computed runs can be recomputed to acquire snapshots. Validated/paid
runs remain finalized and require actual historical evidence for restoration;
no automated restoration tool or live-data backfill is provided. Validate
also blocks legacy computed slips until recomputed. The frontend displays
the structured error's explanatory message.

### Files created or modified

Paths below are relative to `harmonix360/backend/` unless otherwise stated.

- Created `alembic/versions/018_payroll_snapshots.py` and
  `app/services/payslip_snapshot.py` for migration and shared historical reads.
- Modified `app/models/payroll.py`, `app/services/payroll.py`,
  `app/schemas/payroll.py`, `app/api/v1/routers/payroll.py` for snapshot writes,
  response/delivery reads, persisted failed-computation warnings, and firewall
  reporting. Existing public response shapes are preserved.
- Modified `app/services/payroll_context.py`, `app/services/salary_resolver.py`
  for the LOP input and explicit unavailable-input policy; `app/seed.py` for
  the six-rule demo structure.
- Created `tests/test_payroll_gaps.py`: eight collected cases — exactly one
  new golden, one checkout regression, one historical regression, three
  missing/deleted/zero-schedule cases, one legacy recovery case, and one seed
  idempotency case. Modified `tests/test_payroll_api.py` and
  `tests/test_salary_api.py` for the expanded seed vocabulary and explicit
  schedules in finalization fixtures. `tests/conftest.py` cleans up those
  schedules. Existing golden tests were not edited.
- Modified `app/services/dashboard.py` only to recognize the new warning
  codes; dashboard queries were not rewritten. Payrun-only computation
  failures are exposed by the payrun firewall, not the payslip-only dashboard
  warning feed. The same labels were added in
  `frontend/src/types/payroll.ts` and `frontend/src/routes/reports/ReportsPage.tsx`.
  `frontend/src/lib/api-client.ts` now handles structured `detail.message`.
- Root docs: additive edits near PRD B7 (**5 added lines**) and Architecture
  §6/§7 (**7 added lines**) document LOP, missing-checkout firewall, snapshots,
  and the migration policy. This dated section is the handoff update.

### Validation and reproduction

- Focused payroll run: **39 passed**, including the eight new cases.
- Full final run: **337 passed in 73.68 seconds**, zero failures/skips, on the
  isolated migrated and seeded PostgreSQL database (329 existing + 8 new).
- Ruff checks for syntax, undefined names, and unused imports passed on the
  touched Python files; `git diff --check` passed.
- Frontend: `npm ci --no-audit --no-fund`, then `npm run build` passes TypeScript
  and Vite production build. Existing large-bundle advisory remains.
- Migration upgraded the existing development database from 017 to 018 and
  separately upgraded a new `peoplepay360_gap_tests` database from empty to
  head. Existing development payroll records were preserved.
- Preliminary full run on the populated development database: 334 passed,
  three failed — the stale seed-name assertion (updated), plus two existing
  list/RBAC tests which demand 200 and encountered retained legacy payslips.
  Those two tests were not weakened; they run unchanged on the isolated test
  database. The legacy 409 behavior is explicitly covered by the new regression.
- The first empty-database run lacked seeded actors before attendance tests
  ran: five existing actor-reference assertions failed (332 passed). Run
  `python -m app.seed` BEFORE the suite, as in normal application bootstrap;
  the auth module's local seed fixture runs too late for earlier modules.
  No attendance/auth logic was changed to hide this prerequisite.
- Reproduce from `harmonix360/backend`: set `DATABASE_URL` to the application
  role on an isolated PostgreSQL test database, `MIGRATION_DATABASE_URL` to
  its migration role, and `REDIS_URL` to local Redis. Run
  `python -m alembic upgrade head`, `python -m app.seed`, `python -m pytest -q`.
  The final local run uses `peoplepay360_gap_tests`, PostgreSQL 5432 and Redis
  6379. The shared original virtualenv supplied Python; imports came from
  this worktree. No `.env`, live API keys, or platform integrations changed.

Next agent: fetch this branch or its eventual merge on origin/dev before
starting. Apply migration 018 before running the new code. Do not infer that
PDF/email delivery or legacy-history restoration has been implemented.

---

## 2026-09-05 — Gap-fix integration into dev and legacy-409 verification

### Integration history

Fetched origin and read `origin/dev:progress.md` before integrating. The
integration tip was still exactly
`276ce889339123a4b55e834ccb3b283c1753cabb`; no intervening dev commits existed.
The log/code comparison confirmed Phases 1–4 and 6, with the already-reported
snapshot bug addressed by the pending gap-fix branch. `origin/phase-4-gap-fixes`
was `1c98d0d1e338f9fdd84ecbc5629088d6d87fbe27`, a direct descendant.

Fast-forwarded local **dev** from `276ce889` to `1c98d0d` using
`git merge --ff-only origin/phase-4-gap-fixes`. No squash, rebase of gap-fix
history, conflict resolution, or dashboard product patch was needed. The
verification/docs commit following it preserves `1c98d0d` as its parent.

The original OneDrive checkout still contained an empty stale rebase marker;
Git could not remove it. Integration therefore used a fresh clone at
`C:\Users\csepr\peoplepay360-integration`, checked out on dev. Tests import
that clone's source. The original IDE checkout was not reset or advanced;
use the integration checkout or safely resolve its stale Git state before
updating the original checkout. Its local progress.md is not the new dev
handoff until updated from origin.

### Check 1 — seed versus retained legacy demo payslips

**The seed itself is clean:** `app.seed` creates only demo users, departments,
salary rules and the salary structure. It creates no Employee, Contract,
Payrun, Payslip, or PayslipLine. It cannot emit a pre-018 payslip. A new
regression runs the seed twice with existing legacy payslips and compares
every persisted payslip column before/after: no rows are created or rewritten.
The subsequent detail request still returns the documented 409.

**Retained local demo history has a real compatibility limitation:** the
development database `peoplepay360` contains 18 undeleted snapshot-less
payslips for March 2025, from earlier manual payroll verification:

- `prun_dV2D442m`: 9 paid payslips.
- `prun_YxMEAvM5`: 9 computed payslips.

These are not generated by the seed. Re-running seed does not repair them.
An HTTP read of a retained paid slip was explicitly verified to return 409
`historical_snapshot_unavailable`. No historical rows were deleted, repriced,
or backfilled during integration. Recompute the computed run only after its
inputs are corrected; paid history still requires historical evidence. Use
a separate migrated/seeded database and newly computed payrun for a fresh
demo. README now calls this out beside setup/test instructions, including
the fact that one legacy row makes its payslip list page return 409.

### Check 2 — dashboard response with unreadable payslip detail

**Clean; no dashboard bug surfaced.** The React dashboard calls only
`GET /dashboard/summary`. The service reads persisted `Payslip.net_amount`
and `.warnings` through SQL/ORM, not through a payslip-detail HTTP request
or `payslip_response`. It therefore neither receives nor needs to swallow
the detail serializer's 409. The unreadable row's stored money is still
included, and stored warning code/severity/message/references are preserved.

A new regression proves the interaction using a real legacy payslip:
detail 409, summary 200, Engineering paid net `7000.00` and per-employee
average `3500.00`, all three of that payslip's stored warnings preserved,
and a repeated detail response byte-identical to the initial 409.

A separate read-only HTTP check against the RETAINED development records
returned detail **409** and March dashboard **200** with paid net
`376200.00`, average `41800.00`, 18 generated payslips, and 9 warning entries.
The aggregate remains available even with all 18 legacy slips lacking
snapshots. The dashboard does not synthesize a missing-snapshot warning
for each row; this check verifies its existing stored-warning feed, not a
new warning-generation policy.

### Verification and changed files

- Created `harmonix360/backend/tests/test_gap_integration.py`: two regression
  tests covering seed preservation and dashboard/detail-409 independence.
  Both passed in the focused run.
- Modified only root `README.md` and `progress.md` beyond those tests.
  Every application and migration file is unchanged from `1c98d0d`.
- Full suite on merged dev: **339 passed in 79.15 seconds**, no failures or
  skips (337 inherited gap-fix tests plus 2 integration regressions).
- Frontend: `npm ci --no-audit --no-fund` and `npm run build` passed TypeScript
  (`tsc -b`) and Vite production build. Existing large-bundle advisory remains.
- New-test Ruff check and `git diff --check` passed.
- Backend environment: real PostgreSQL database `peoplepay360_gap_tests`,
  application role, migrated to 018; Redis on 6379. Explicitly ran
  `python -m alembic upgrade head` and `python -m app.seed` before the suite.
  The populated development database was used for read-only compatibility
  checks, not as the clean test fixture database. Its known legacy 409s are
  preserved and explicitly reported above.

This is integration and verification only. Phase 5 PDF/email rendering and
automated historical-snapshot restoration remain unimplemented.

---

## 2026-09-05 — Phase 5: shared payslip PDF and bulk email (PS B8)

### Starting point and scope

Fetched origin, read `origin/dev:progress.md` and the most recent 30 commits,
and compared the documented phases with actual code before making changes.
No new documentation/code discrepancy was found. Created
`phase-5-payslip-pdf-email` from integration tip
`cd823a17799c7d651066a51053e8db3e296ff3a2`, which includes gap-fix `1c98d0d`
and the subsequent legacy-409 verification. Read PRD §4 B8, Architecture §1,
the actual Payslip/PayslipLine models, historical serializer, and existing
router/Taskiq patterns including Phase 4's exact `send_payslips` payload.

Work lives at `C:\Users\csepr\peoplepay360-integration`. The original OneDrive
IDE checkout still has the previously documented stale Git state; it was not
reset or modified. This phase is committed and pushed on its own branch;
it must be integrated before a later agent assumes Phase 5 exists on dev.
Phases 1–4 and 6 were inherited; Phase 5 is implemented below. Phase 7 and
the optional platform phases are not implemented by this change.

### Implemented behavior and decisions

- **One document source:** `templates/payslip.html.j2` renders both View
  calculation's HTML iframe and WeasyPrint's PDF. It renders every persisted
  PayslipLine in sequence, with name, code, category and amount, including
  `PP360_LOP`. Both renderers use the same embedded DejaVu regular/bold fonts
  to avoid host font substitutions. Decimal amounts are formatted, never
  recomputed. Gross/net, wage, employee and payrun references come from the
  existing historical serializer. Jinja autoescaping is enabled; PDF rendering
  allows only embedded font data and cannot fetch HTTP or filesystem resources.
- **Preview/print:** `GET /api/v1/payslips/{id}/preview` supports computed
  history for the existing View calculation flow. `GET .../{id}/pdf` returns
  an attachment only for validated/paid slips (otherwise 409). Admin, HR
  Payroll Manager and HR Payroll User may use the document/status endpoints;
  Employee and HR Manager get 403, matching payroll permissions. Warnings are
  excluded from template input and stay in the surrounding application UI.
- **Bulk delivery:** the existing paid-only POST
  `/api/v1/payruns/{id}/send-payslips`, its 202 response, and its enqueue
  payload are unchanged. New Taskiq task `send_payslips` consumes exactly
  `{payrun_id, payrun_name, period_start, period_end, payslips}` with existing
  `{payslip_id, employee_id, work_email, net_amount, gross_amount}` entries.
  Monetary payload values remain strings. The dispatcher publishes one
  `deliver_payslip` job per employee; each persisted delivery retains the
  same contract with a one-item payslips array. Recipient and amounts are
  verified against the stored snapshot before rendering/sending.
- **Independent status:** migration 019 adds `payslip_deliveries`, separate
  from immutable payroll data. One row per payslip records pending/sent/failed,
  error, attempts and timestamps. Pending rows commit before child publication.
  An individual enqueue, address, render or SMTP failure does not stop other
  employees. `GET /api/v1/payruns/{id}/deliveries` drives the Payrun table's
  polling status column. Before first dispatch the UI shows Not queued.
- **Retries:** repeated Send skips sent rows and requeues pending/failed ones.
  Row locks serialize duplicate child execution through SMTP/result commit.
  A stopped worker can leave a pending row; Send requeues it. `sent` means
  SMTP acceptance, not confirmed inbox delivery. SMTP has no exactly-once
  transaction with PostgreSQL: a crash after acceptance but before commit can
  duplicate mail on explicit retry. No automatic recovery daemon was added.
  Recipient addresses remain historical snapshots; changing today's employee
  email does not silently redirect an already-paid payslip.
- **Local mail:** MailHog is in both Compose profiles, with loopback SMTP 1025
  and mailbox UI 8025. Compose defaults SMTP_HOST to mailhog; native processes
  default to localhost. `.env.example` intentionally leaves SMTP_HOST commented
  so copying it does not replace the Compose hostname with localhost. Worker
  startup waits for the backend to finish migrations/seed. The backend image
  includes Pango/Harfbuzz/DejaVu and installs the pinned requirements export.
- **Legacy history:** migration 019 does not rewrite Payslip or PayslipLine.
  Snapshot-less historical documents retain the explicit migration-018 409;
  no restoration/backfill or deletion of paid demo history was performed.

### Files created or modified

Backend paths below are relative to `harmonix360/backend/`:

- Created `templates/payslip.html.j2`, `app/services/payslip_documents.py`,
  and `app/api/v1/routers/payslip_documents.py` for shared rendering and reads.
- Created `app/models/payslip_delivery.py`,
  `alembic/versions/019_payslip_delivery.py`,
  `app/services/payslip_delivery.py`, and `app/jobs/tasks/payroll_jobs.py`
  for delivery state, independent jobs and SMTP.
- Created `tests/test_payslip_documents.py` (10 collected cases) and the
  opt-in `scripts/verify_payslip_delivery.py` local acceptance harness.
- Modified `app/models/__init__.py`, `app/main.py`, and `app/core/config.py`
  only to register the new model/router and configure SMTP/fonts.
- Modified `pyproject.toml`, `uv.lock`, `requirements.txt`,
  `requirements-dev.txt`, and `Dockerfile` for Jinja2/WeasyPrint/aiosmtplib,
  dev-only PDF/SMTP verification dependencies, and native rendering libraries.
  Created `.dockerignore` to keep generated payroll artifacts and local
  environments/caches out of the backend image.
- Frontend: created `src/hooks/usePayslipDocuments.ts` and
  `src/routes/payroll/PayslipDocument.tsx`; modified
  `src/routes/payroll/PayslipDetail.tsx` to use the shared document,
  `src/routes/payroll/PayrunDetailPage.tsx` to display email status, and
  `vite.config.ts` to allow an optional local API proxy port override.
- Root: modified `docker-compose.yml`, `.env.example`, `.gitignore`
  (generated artifacts), `README.md` (setup, endpoints, retries and acceptance
  reproduction), and this `progress.md` handoff.

### Tests and actual UI/SMTP verification

- Full suite: **349 passed in 185.64 seconds**, zero failures/skips
  (339 inherited + 10 new); only the inherited passlib/crypt deprecation remains.
  The new cases cover shared-template PDF/HTML equality and ordered LOP lines,
  warnings absent from documents, computed print rejection, validated/paid
  downloads, byte-identical HTML/PDF after successful live wage/name changes,
  five-role endpoint allow/deny, legacy 409, a real SMTP 550 failure isolated
  from two successful sends, concurrent duplicate child execution, retrying
  only failed deliveries, child enqueue failure isolation, and unpaid dispatch
  rejection. Existing payroll golden tests were not modified.
  Final focused run after strengthening successful-mutation assertions:
  **10 passed in 41.09 seconds**.
- Frontend `npm run build` passes TypeScript (`tsc -b`) and Vite production
  build. Existing large-bundle advisory remains. Black and targeted Ruff checks
  pass for all new Python files; `git diff --check` passes. Both core and
  advanced Compose configurations validate, including with `.env.example`.
  Built the backend image successfully with pinned dependencies.
- Migrated two separate PostgreSQL databases from empty through 019 and seeded
  before testing. Full suite uses `peoplepay360_phase5_suite` with the application
  role and Redis database 6. `peoplepay360_phase5_tests` holds only synthetic
  manual demo fixtures and uses Redis database 5. PostgreSQL/Redis host ports
  are 5432/6379. Neither the retained legacy development history nor live
  `.env` credentials were modified.
- Real UI verification used Edge through Playwright against Vite on 5176 and
  backend on 8015, logged in as HR Payroll Manager. The actual payrun
  `prun_OxKqzQgA` has three real employees and paid payslips. Opened View
  calculation, inspected the shared preview, downloaded using Print Payslip,
  and confirmed downloaded bytes equal the API-generated sample. Visually
  inspected the PDF page and preview: all six rules and amounts match.
  Example Asha payslip `pslip_jqQ4jL2l`: March 2025, wage `30000.00`, 21 schedule
  working days, 3 unpaid days, LOP `4285.71`, HRA `12000.00`, gross `42000.00`,
  professional tax `200.00`, net `37514.29`. No warnings appear on the document.
- Clicked Send payslips through the UI with the actual Redis/Taskiq worker.
  **Two employees sent, one failed**, with a clear SMTP rejection on only the
  failing row and no browser page errors. MailHog's API independently confirms
  exactly two accepted fixture recipients and exactly one real PDF attachment
  per message, each containing the LOP code/amount and expected net.
  MailHog normally accepts every address, so a local SMTP proxy deliberately
  returns 550 for the fixture's nonexistent third mailbox and forwards the
  other two to MailHog. This is explicit protocol-level fault injection, not
  a claim that MailHog checks whether mailboxes exist. No external SMTP was used.
- Local ignored evidence under `harmonix360/backend/artifacts/`:
  `phase5-sample.pdf`, `phase5-preview.html`, `phase5-pdf.png`,
  `phase5-ui-download.pdf`, `phase5-ui-preview.png`,
  `phase5-preview-document.png`, `phase5-ui-deliveries.png`, and
  `phase5-fixture.json`. Recreate with the committed opt-in harness; generated
  synthetic payroll artifacts are not committed as application assets.

### No Phase 4/6 interference and next-agent guidance

Diff against the starting integration commit confirms **no changes** to
payroll computation/context/resolver, existing payroll model/schema/router,
historical serializer, send enqueue contract, seed salary rules, or any Phase 6
dashboard backend/frontend file. Changes to existing payroll React screens
only connect shared document rendering and show delivery state. All inherited
339 tests continue passing. Platform/intelligence integrations remain dormant.

Apply migration 019 and rebuild backend/worker dependencies before using this
branch. Fetch this branch or its eventual integration on origin/dev before
continuing. Use a fresh migrated/seeded database for demos with printable
payslips; migration 018 still cannot reconstruct missing historical snapshots.
For the local acceptance fixture, README and the script docstring explain the
SMTP rejection proxy and isolated worker configuration. Phase 7 and optional
platform work remain outside this phase.
---

## 2026-09-05 — Phase 7 preparation (seed LOP scenario, partial RBAC audit, roadmap skeleton)

### Starting point and protocol

- Fetched `origin` and read `origin/dev:progress.md`, then diffed its claims
  against the code on `origin/dev` before touching anything. Base commit:
  `cd823a17799c7d651066a51053e8db3e296ff3a2` (gap-fix integration).
- Claims checked and **all confirmed**: `LOP_AMOUNT` present in
  `SEED_CONTEXT_NAMES`; `lop_schedule_unavailable` emitted as a blocking
  warning; `app/seed.py` creating departments/users/salary-structure and **no**
  Employee/Contract/Payrun/Payslip; migration `018_payroll_snapshots` adding
  exactly the three nullable JSONB columns; suite at **339 passed**. No
  discrepancy between progress.md and the code was found.
- Branch `phase-7-prep`, created from that tip. No earlier phase-7-prep work
  existed to rebase — `git branch -a --list "*phase-7*"` was empty, and
  progress.md had never mentioned Phase 7, `ROADMAP.md`, or
  `docs/rbac-audit-partial.md`.
- **Stale rebase state cleared.** The OneDrive checkout still held an empty
  `.git/rebase-merge` directory and a `REBASE_HEAD` from an earlier session
  (both previous handoffs noted Git could not remove them). The lock had since
  released; both were removed, and this checkout is usable again. No commits,
  branches or worktrees were affected — the directory was empty.

### 1. Demo seed — Loss-of-Pay scenario

`_seed_lop_scenario` in `app/seed.py` creates the INPUTS for a payrun that
exercises Gap-fix's `LOP_AMOUNT` rule end to end:

| Input | Value | Why this value |
|---|---|---|
| Employee | `lop.demo@peoplepay360.com` (Lakshmi Prasad, Engineering) | — |
| `bank_account` | `IN00PP360DEMO0001` | Without it every payslip carries a blocking `missing_bank_details` warning and the demo run can never be validated |
| Working schedule | Mon–Fri 09:00–17:00, 60 min break (35 h/week) | Without a schedule `LOP_AMOUNT` is OMITTED and a blocking `lop_schedule_unavailable` fires |
| Contract | 30,000.00, active, from 2026-01-01, open-ended | Open-ended so no `contract_gap` warning |
| Leave type | `PP360_UNPAID`, `payroll_integration=True`, `requires_allocation=False` | The flag is what makes an approved absence reach `UNPAID_LEAVE_DAYS` at all |
| Approved absence | 2026-08-12 .. 2026-08-14 (3 days) | All three are scheduled workdays |
| Attendance | 18 days, 09:00–16:00 | Exactly the 7 NET scheduled hours, so status derives as PRESENT not OVERTIME; every day checked out, so no `missing_checkout` |

**Verified by actually computing it**, through the real API on the seeded
database (payrun created, computed, then soft-deleted to leave the database as
found):

```
PP360_BASIC  basic       30000.00
PP360_HRA    allowance   12000.00      (40% of Basic)
PP360_GROSS  gross       42000.00
PP360_PT     deduction     200.00
PP360_LOP    deduction    4285.71      (30000 / 21 scheduled days) * 3
PP360_NET    net         37514.29
worked_days 18.00   warnings []   blocking {}
```

Those are deliberately the same figures as the gap-fix phase's hand-computed
golden test, so the number on the demo screen and the number pinned in the
suite are the same arithmetic. **Zero warnings, blocking or advisory** — the
scenario can be computed → validated → marked paid without a firewall stop,
which is the point of a demo dataset.

**Idempotency proven by running the seed twice**: second run reports
`nothing (already seeded)`, and row counts are unchanged
(1 employee / 1 contract / 1 schedule / 1 leave type / 1 request / 18
attendance rows). This matters more than usual here: two overlapping active
contracts are refused by `contracts_active_period_overlap_excl`, so a
non-idempotent seed would not merely duplicate rows — its second run would
crash.

**The seed still creates no Payrun, Payslip or PayslipLine, deliberately.** A
payslip written by anything other than the compute engine has no
`reference_snapshot`, and migration 018 makes exactly those rows return 409
`historical_snapshot_unavailable` on every read. The seed lays out inputs; a
human presses Compute.

**MARKED TODO, NOT FAKED — "a paid payrun with a generated PDF payslip"**
remains unseeded, in the module docstring and in the seed's own log output. It
cannot be seeded: there is no PDF renderer in this tree, and a placeholder file
or a `pdf_url` pointing at nothing would make a missing feature look present.
When `origin/phase-5-payslip-pdf-email` lands, extend `_seed_lop_scenario` to
compute → validate → mark paid → send through the real endpoints.

No arithmetic was reimplemented: the seed calls `WorkingScheduleService`
(server-computed `weekly_hours`), `request_duration` (Phase 2's leave
duration), `worked_hours` and `derive_attendance_status` (Phase 2's pure status
policy, with the contract's schedule override).

### 2. `docs/rbac-audit-partial.md` — created, not extended

The task said "extend"; the file **did not exist** on `origin/dev` and
progress.md had never claimed one. It is created here, covering what has
actually been verified.

Every cell is an **observed HTTP status code** from a real in-process API call
per role (token minted with `create_access_token`, the same helper
`tests/conftest.py` uses), not a reading of the source. All GETs, so the audit
changes nothing it measures.

| Route | employee | hr_manager | payroll_user | payroll_manager | admin |
|---|---|---|---|---|---|
| `GET /dashboard/summary` (default period) | 403 | 403 | 200 | 200 | 200 |
| `GET /dashboard/summary` (explicit period) | 403 | 403 | 200 | 200 | 200 |
| `GET /dashboard/summary` (+employee_type filter) | 403 | 403 | 200 | 200 | 200 |
| `GET /payruns/` | 403 | 403 | 200 | 200 | 200 |
| `GET /payslips/` | 403 | 403 | 200 | 200 | 200 |
| `GET /salary-structures/` | 403 | 403 | 200 | 200 | 200 |

All match Architecture §5. Three findings are recorded in the document rather
than smoothed over:

1. **§5's matrix has no explicit Dashboard/Reports row.** The dashboard is a
   READ over Payruns/Payslips, which is the row that governs it and why
   `PAYROLL_ROLES` is the correct gate — HR Manager and Employee get no access,
   not even read.
2. **Unauthenticated `GET /dashboard/summary` returns 200, not 401**, because
   `get_current_user` falls back to the demo admin when `ENVIRONMENT` is not
   `production`. Bounded and deliberate, but anyone deploying must set
   `ENVIRONMENT=production`. Verifying the production branch actually 401s is a
   deployment-config test, not covered.
3. **`POST /payruns/{id}/send-payslips` was not probed** — it has side effects
   (it publishes to the broker) and this audit is read-only. Its gate is
   covered by `tests/test_payroll_api.py`.

**PENDING PHASE 5 rows**, listed with no status codes because none have been
observed: payslip PDF print/download, bulk payrun print, the `send_payslips`
worker, and employee self-service payslip access (deliberately absent per PRD
§3 — if Phase 5 adds an endpoint, that is a scope change needing its own §5
row). The document also states plainly what it does NOT cover: write verbs,
row-level "own records only" scoping, and MCP/offline-sync entry points.

### 3. `ROADMAP.md` — skeleton only

Structure and section headers, with a header banner saying in as many words
that it must not be read as status. Sections whose phases do not exist carry
`<!-- TO BE FILLED -->` rather than prose, because filling them in early is
precisely how a roadmap starts claiming features nobody built. Phase 5 is
marked **IN FLIGHT** (branch exists, not integrated); Phases 7–10 **NOT
STARTED**. It carries the Phase 4 → Phase 5 handoff contract (task name and
payload) and the two constraints Phase 5 must not violate (render from the
snapshot, surface the legacy 409). Cross-cutting invariants are listed once.

### One test changed, and why it is not a product change

`test_attendance_health_is_schedule_derived_not_hardcoded` asserted
`expected_working_days == 15` against the **org-wide, unfiltered** aggregate.
Adding an active employee with a working schedule to the seed made it 20. The
dashboard is right — it counted the employee because the employee exists; the
assertion was coupled to a database containing nothing but its own fixture.

Scoped to the fixture's own department, exactly as the three sibling tests were
in the Phase 4/6 integration for the identical reason. Within Engineering:
alice 5/3, bob 5/5, dave/erin/frank 0/0 → **8 of 10 = 80.00%**, the same
percentage as before. No dashboard, payroll, PDF or email file was touched.

### Files created or modified

- Modified `harmonix360/backend/app/seed.py`: the LOP scenario, its constants,
  the Phase 5 TODO, and log output naming the employee and the expected
  figures.
- Created `docs/rbac-audit-partial.md` and `ROADMAP.md`.
- Modified `harmonix360/backend/tests/test_dashboard.py`: one test scoped by
  department (above).
- **Not touched:** any dashboard service/schema/router file, any payroll
  computation file, any PDF or email file. Nothing was merged into `dev`.

### Validation record

- Full suite on the isolated, migrated (018) and seeded database
  `peoplepay360_gap_tests`: **339 passed**, zero failures, zero skips.
- Baseline on the same database before any change: **339 passed** — identical,
  so this branch adds no tests and breaks none.
- On the shared development database `peoplepay360` the suite is **337 passed,
  2 failed**; both failures are the documented pre-existing legacy-payslip
  409s (`test_domain_skeleton.py` payslip-list rows), caused by 18
  snapshot-less payslips retained from earlier manual verification. Unchanged
  by this branch and already recorded in the gap-fix handoff.
- Seed run twice against the test database: idempotent, verified by row counts.
- Scenario computed through the real API and matched the documented figures
  exactly; the verification payrun was soft-deleted afterwards.
- `ruff check app/seed.py --select F,E9`: all checks passed.

### Explicitly still pending Phase 5

1. A paid payrun with a generated PDF payslip in the demo seed.
2. Print / download payslip PDF, and bulk payrun print — RBAC rows unverified
   because the routes do not exist.
3. The `send_payslips` worker; the enqueue boundary exists and nothing consumes
   it.
4. Whether employees ever get self-service payslip access. Currently and
   deliberately: no.

---

## 2026-09-05 — Phase 7 closeout: the core is demoable

### Protocol and starting point

Fetched origin and read `origin/dev:progress.md` as it stands on the remote,
then diffed its claims against the code on `origin/dev` before touching
anything. Integration tip: **`cd823a17799c7d651066a51053e8db3e296ff3a2`**
("test(integration): verify gap-fix legacy reads and document demo limits").

**No discrepancy was found.** progress.md's claims were checked file by file
against `origin/dev`: Phases 1-4 and 6 present, gap-fix's `LOP_AMOUNT` and
`018_payroll_snapshots` present, `payslip_snapshot.py` present, Phase 5 absent
exactly as documented, `app/services/sync_entities.py` present and registering
zero entities. The documented suite total (339) was reproduced.

Two things worth recording that the handoff did not mention:

- Local `dev` was accidentally sitting on `origin/phase-5-payslip-pdf-email`'s
  tip (`c896c34`), one commit ahead of `origin/dev`. Work was branched from the
  **remote** `origin/dev` tip regardless, per protocol.
- The working tree carried an **uncommitted fix to `app/jobs/broker.py`** that
  is on no branch. It sets `socket_timeout=None` on the Taskiq broker: redis-py
  8.x defaults every connection to a 5s read timeout, so the worker's idle
  `BRPOP` timed out every 5 seconds, `taskiq_redis`'s `listen()` catches only
  `ConnectionError` and not `TimeoutError`, and the worker process crashed and
  respawned in an infinite loop with no task in flight. **Phase 5's bulk email
  cannot work without this fix**, so it is carried forward and committed here
  rather than discarded.

### Branch and integration

Branch `phase-7-closeout`, created from `cd823a1`. Both branches to merge had
their merge-base **at that exact commit**, so the requested rebase was a
verified no-op rather than a skipped step:

```
merge-base origin/dev origin/phase-5-payslip-pdf-email = cd823a1
merge-base origin/dev origin/phase-7-prep              = cd823a1
origin/dev tip                                         = cd823a1
```

`--no-ff` merges of both. The only conflict was `progress.md`, where each branch
had appended its own dated section; both were kept, in order, separated by a
rule. Nothing was dropped. The union was then verified mechanically rather than
by eye: `git diff origin/phase-5... HEAD` shows *only* phase-7-prep's files and
`git diff origin/phase-7-prep HEAD` shows *only* phase-5's, which is exactly
what a clean union looks like.

### 1. The seed now contains a finalized payrun with a real PDF

`app/seed.py`. The previous version's own TODO said a paid payrun "cannot be"
seeded because no PDF renderer existed. Phase 5 landed, so it can be.

- `_seed_paid_payrun` drives **July 2026** through the real `PayrunService`:
  `create_payrun` then `compute` then `validate_payrun` then `mark_paid`, the
  same four calls PS B6's buttons make. Nothing is written through repositories
  or SQL: a payslip written by anything but the compute engine has no
  `reference_snapshot`, and migration 018 makes exactly those rows answer 409 on
  every read, so a hand-built "paid" payslip would seed a demo whose payslips
  cannot be opened, printed or emailed.
- `_verify_pdf` then **renders** one of the resulting payslips and checks the
  bytes start with `%PDF-`. "A paid payrun with a generated PDF" is generated,
  not asserted. It never raises: the seed is the backend container's start
  command (`alembic upgrade head && python -m app.seed && uvicorn ...`), so an
  exception there would take the whole API down over a document-rendering
  dependency. A failure logs an ERROR naming the cause, and the data stays correct.
- **July, not August, and that is the point.** August 2026 is the live demo
  period. A pre-computed August would hand every employee a blocking
  `duplicate_payslip` at Compute - the firewall working correctly and the demo
  failing anyway.
- `_seed_roster` adds four more employees across the four seeded departments
  (Engineering, Sales, HR, Finance), each with a schedule, a bank account and an
  open-ended active contract. PRD §11 asks for "representative data", and B9's
  Salary Cost by Department and department breakdown are named acceptance
  criteria that render as a single bar - and read as broken - against a
  one-employee database. One roster member is `CONTRACT` type so the
  employee-type filter demonstrates something.
- `_seed_attendance_window` was extracted from the old inline loop and now seeds
  July and August for everyone, skipping the approved unpaid leave.

**The employee login is now linked to its Employee row** (`EMPLOYEE_LOGIN_EMAIL`).
This closes a real hole: no endpoint sets `Employee.user_id` - `EmployeeCreate`
excludes it deliberately, so an HR Manager cannot attach an employee to an
account outranking their own, and the Admin surface that would replace it does
not exist (see known gaps). Without the link the token carries no `employee_id`
claim, every "own records" screen has nothing to scope by, and demonstrating the
Employee role requires an `UPDATE` typed into psql - which PRD §7 explicitly
rules out. The link is guarded: if another Employee already holds that login it
logs a warning and leaves it alone, because `user_id` is UNIQUE and stealing it
would raise. The demo user's display name was changed to match the Employee it
points at.

Idempotency is preserved throughout - verified by running the seed twice against
a fresh database and diffing every persisted payslip column.

### 2. Full RBAC audit - 395 observed status codes, all matching §5

`docs/rbac-audit.md`, produced by `harmonix360/backend/scripts/rbac_audit.py`
(committed, so the audit is reproducible rather than a screenshot).

**79 endpoints x 5 roles = 395 probes. Every cell matches Architecture §5**,
including Reports/Dashboard and all four B8 Print/Send routes, which
`docs/rbac-audit-partial.md` had listed as PENDING PHASE 5. That file is
superseded and kept for its history.

Every cell is an observed status code from a real request through the real ASGI
stack, with tokens from a real `POST /auth/login` round-trip per role. Nothing
is inferred from reading `require_role` in the source.

**The audit's first version was wrong, and the way it was wrong is worth
recording.** It sent empty `{}` bodies to service-gated routes. Those return 422
at request validation *before* the handler runs, so `require_hr` was never
reached and seven routes reported false mismatches - the probe was measuring
Pydantic, not authorisation. The fix was to send schema-valid bodies pointing at
well-formed but nonexistent public ids: `require_hr(user)` is the first
statement of every such method, before the row lookup and before the version
check, so a denied role gets 403 and a permitted one gets 404, and no UPDATE is
reachable either way.

Two more findings, recorded rather than smoothed over:

- **`404`, not `403`, on another employee's record.** `assert_can_read` returns
  "not found" on purpose: a 403 confirms the row exists and would let any login
  enumerate the staff directory by probing ids. The audit classifies this as
  `hidden` - a denial with no existence oracle - rather than counting it as a
  pass or a failure.
- **The four `/me` routes answer 404 for every HR role**, because those logins
  have no Employee row. That is the normal shape of a payroll or admin account,
  not a grant and not a denial; the audit classifies it as `no-link`.

The audit is non-mutating except for §5's two Employee *grants* (create own
attendance, create own leave request), which cannot be observed without being
exercised. Both rows are created as the employee and deleted as admin, and a
failed cleanup is reported as an audit failure. A residue check after a full run
against a freshly seeded database found exactly the seeded dataset and nothing
else.

### 3. Both PRD acceptance scenarios, run live end to end

`harmonix360/backend/scripts/demo_scenarios.py`, over real HTTP against a
running stack - real logins per role, real `Idempotency-Key` headers, the real
Taskiq worker and the real SMTP catcher. **Both pass, with no manual DB edits,
in 7.8 seconds.**

Scenario 1 (employee, schedule, contract, payrun, payslip, PDF, email) asserts,
among other things: server-computed weekly hours of 35.00; the new employee
appearing in B5 step 2's eligibility list; the hand-checkable breakdown BASIC
60000.00, HRA 24000.00, GROSS 84000.00, PT 200.00, LOP 0.00, NET 83800.00; the
payslip carrying the snapshot Compute wrote; a real 12.6 KB PDF from
`GET /payslips/{id}/pdf`; the delivery row reaching `sent`; and the SMTP
catcher's mailbox count increasing.

Scenario 2 (allocation, request, approval, balance) asserts that a **pending
request does not reserve balance**, that HR sees it in the approval queue, that
approval moves the allocation to taken 3.00 / remaining 7.00, and that the
approved request names the allocation it debited.

Three assertion bugs in the script were fixed rather than worked around, and
each taught something about the API worth writing down:
`POST /payruns/{id}/validate` answers with the **validation report**, whose
`status` field is the status the run had when the checks ran (so it reads
`computed` on success - the transition is confirmed by re-reading the run);
`duration` is an unquantized `Decimal` on the wire (`"3"`) while allocation
columns are stored quantized (`"3.00"`), so comparisons are made as `Decimal`,
never as text; and the pending status is `to_approve`, not `pending`.

The teardown reports three 409s as **refused by design** rather than as
failures: approved leave is immutable, a used allocation cannot be deleted, and
a referenced leave type cannot be removed. A finalized payrun likewise cannot be
deleted, so a rehearsal cannot fully undo itself - the script says so instead of
pretending otherwise, and rehearsals should target a scratch database.

### 4. Both failure modes verified in the browser

Driven through the **built frontend** with Playwright, not by reading code:

- **Duplicate payslip.** Computing a second payrun over the already-paid July
  period surfaces blocking `duplicate_payslip` findings in the validation panel,
  and **Validate is observed disabled** while they stand.
- **Overlapping active contract.** Creating a second active contract for the
  Loss-of-Pay employee is refused by the Postgres EXCLUDE constraint and
  rendered as an inline form error naming the conflicting contract and its
  period: "Lakshmi Prasad already has an active contract (ctr_...) covering
  2026-01-01 to open-ended, which overlaps the requested period...". Not a
  crash, not a blank screen.

### 5. UI pass - every B1-B9 feature has a real screen

**29/29 browser checks passed, zero HTTP 5xx.** No `SectionStub` remains routed
(`SectionStub.tsx` is kept for a future section that lands ahead of its
implementation). B7's rule-by-rule breakdown renders inside the payslip dialog's
sandboxed preview iframe - the same server template the PDF and the email
attachment use - showing sequence, rule, code, category and amount for all six
rules, plus the contract-wage snapshot and the pay period.

Three things were fixed during the pass:

- The frontend container image predated the Phase 5 merge, so **Print Payslip
  did not exist in the running app**. Rebuilt; "B8 Print produces a downloaded
  PDF file" is now an observed download event.
- The browser tab still read `Harmonix360 - Enterprise Platform`. Retitled to
  `PeoplePay360 - HR & Payroll`; the product name was visible on stage.
- **`MyProfilePage` is new.** PRD §4's Employee user stories open with "view own
  profile", and nothing in the frontend called `/employees/me` - the endpoint
  existed with no screen behind it. The page is read-only (§5 gives Employee R
  and nothing more), reads `/employees/me` and `/time-off-allocations/me` so
  there is no id in the URL to point at someone else, and renders an explanation
  rather than an error for a login with no Employee row. Its nav entry is
  Employee-only: HR roles reach anyone through Employees, and their logins
  usually have no Employee row, so the entry would lead them nowhere.

### 6. ROADMAP.md finished

Replaced the skeleton with real content in every section: what each phase
delivered and the decision that mattered in it, the two forward phases described
concretely enough to start from, the six cross-cutting invariants, and a
**known-gaps table** of nine deliberate omissions with the reason each is open.
The gaps include the one Architecture §5 row with no implementation behind it
(user management), which is also why `Employee.user_id` has to be seeded.

### Files created

- `docs/rbac-audit.md` - the complete audit, 395 cells.
- `harmonix360/backend/scripts/rbac_audit.py` - the reproducible probe.
- `harmonix360/backend/scripts/demo_scenarios.py` - both PRD §7 scenarios.
- `harmonix360/frontend/src/routes/profile/MyProfilePage.tsx` - "view own profile".

### Files modified

- `harmonix360/backend/app/seed.py` - roster, paid payrun, PDF verification,
  employee-login link, attendance-window helper.
- `harmonix360/backend/app/jobs/broker.py` - the Redis `socket_timeout` fix
  described above (was uncommitted, on no branch).
- `harmonix360/backend/tests/test_gap_integration.py` - see below.
- `harmonix360/frontend/src/router.tsx`, `src/lib/navigation.ts`, `index.html` -
  My Profile route, nav entry, product title.
- `ROADMAP.md`, `progress.md`.

### The one test that had to change, and why it is not weaker

`test_seed_does_not_create_or_rewrite_legacy_payslips` asserted that the whole
`payslips` table was byte-identical after running `seed()` twice. The seed now
has its own paid demo payrun, so that assertion would be asserting the demo
dataset does not exist. It is replaced by the two claims that actually matter,
both **stronger** than the old equality in the dimension that counts:

1. **Preservation** - every row that existed before the seed ran is identical
   afterwards, compared column by column. This is the real content of migration
   018's promise: finalized history is never rewritten, repriced or backfilled.
2. **Idempotence** - the second `seed()` adds nothing the first did not. A seed
   that computed a fresh July on every run would duplicate payslips for one
   period, which is precisely what `duplicate_payslip` exists to catch.

### A second test changed: a genuinely racy lock assertion

`tests/test_locks.py::test_same_entity_serializes` failed once in three full
runs, on a **0.65 millisecond** inversion. It is inherited from the platform
baseline, and its own docstring claims it asserts on ordering rather than
elapsed time "so a slow machine makes the test slower rather than flaky". That
claim was false for one of its assertions.

Both coroutines share one event loop. `holder_committed` is appended only when
the loop resumes the holder **after** its COMMIT round-trip returns — but
Postgres releases the advisory lock *at* COMMIT, so the contender can unblock
and append `contender_acquired` in between. The assertion
`contender_acquired >= holder_committed` was therefore comparing scheduling
order, and proved nothing about the lock.

It now compares against `holder_about_to_commit`, which is logged **inside**
the transaction while the lock is still held, and adds an assertion the old
version could not make: that the contender actually **blocked for the whole
hold** (`waited >= HOLD_SECONDS * 0.9`). That is stronger than an ordering
comparison, not weaker — scheduling order cannot fake a 0.75-second wait. Six
consecutive runs of the module pass.

No other test was modified. No assertion anywhere was relaxed.

### Validation record

- **Backend suite: 349 passed**, zero failures or skips, against a freshly
  created PostgreSQL database migrated to `019_payslip_delivery` and seeded.
  That is 339 inherited plus Phase 5's 10 payslip-document tests.
- **RBAC audit: 395/395 cells match Architecture §5.**
- **Demo scenarios: both PRD §7 scenarios pass end to end**, no manual DB edits.
- **UI pass: 29/29 browser checks, 0 HTTP 5xx.** One `pageerror` is logged: the
  deliberate contract-overlap `ApiError` surfaces as an unhandled rejection in
  the console. The form renders the message correctly; the console noise is
  cosmetic and is recorded rather than hidden.
- **Frontend: `npm run build` passes** `tsc -b` and the Vite production build.
  The pre-existing large-bundle advisory remains.

### Environment notes for whoever runs this next

- **The suite must run in the backend container, not on the Windows host.**
  WeasyPrint needs native Pango/Cairo libraries and the DejaVu fonts at
  `PAYSLIP_FONT_DIR`; the image installs both (`fonts-dejavu-core`,
  `libpango-1.0-0`), a bare Windows host has neither. On the host, five
  `test_payslip_documents.py` tests fail with `NameError: name 'Document' is not
  defined` from WeasyPrint's lazy import - an environment gap, not a defect.
  Reproduce with:

  ```
  docker exec -w /app peoplepay360_backend pip install -r requirements-dev.txt
  docker exec -e DATABASE_URL=<isolated db> -w /app peoplepay360_backend \
    sh -c "alembic upgrade head && python -m app.seed && python -m pytest -q"
  ```

- `pip install -r requirements-dev.txt` inside the backend container adds
  pytest/aiosmtpd/pypdf, which the runtime image does not carry. **It does not
  survive a container recreate** - `docker compose up -d` on any service that
  depends on the backend will drop it, which cost one test run this session.
- Rehearsals were run against an **isolated scratch stack** inside the backend
  container - a second uvicorn on port 8100 and its own taskiq worker, both
  pointed at `peoplepay360_phase7` and **Redis database 1** - so the rehearsal
  worker consumed its own queue rather than competing with the demo worker for
  the shared one. That isolation is the only reason a rehearsal could be run
  repeatedly without polluting the demo data.

### The demo database needs a reset before the real demo

`docker compose`'s `peoplepay360` database is **not pristine**, and this was not
caused by Phase 7's seed:

- Two hand-made employees (`Priya Sharma`, `Rahul Verma`) and a paid
  `September Payrun` predate this session, from an earlier manual verification.
- `employee@peoplepay360.com` was **already linked** to that hand-made Rahul
  Verma, so the seed's link guard correctly refused to move it - which is why
  the My Profile screenshot shows Rahul Verma rather than Lakshmi Prasad.
- One validated `Demo Scenario ... - August 2026` run remains from the first
  rehearsal, which was run against the demo database before the scratch stack
  existed. A finalized run cannot be deleted (PS B6), so it cannot be removed
  through the API. Its employee is soft-deleted and it is not selectable, so it
  does not affect either scenario - but it is clutter with a rehearsal's name on it.

None of this blocks the scenarios. For a clean stage, run
`docker compose down -v && docker compose up -d`, which drops the volume and
re-seeds from scratch. **That was not done here because dropping the volume
destroys data this session did not create**, and that is the owner's call. On a
fresh database the seed produces a fully coherent dataset - verified on
`peoplepay360_phase7`, where the employee login links to Lakshmi Prasad as
designed.

### IS THE CORE DEMOABLE?

**Yes.** Both PRD §7 scenarios run start to finish with no manual database
intervention, in under ten seconds by API and comfortably inside five minutes by
hand. All nine B-features and all seven A-features have real screens wired to
real data. RBAC is enforced server-side for all five roles, proven by 395
observed status codes. Payslip PDF generation and bulk email work end to end,
verified by a downloaded PDF in the browser and a message in the SMTP catcher.
Both required failure modes surface cleanly in the UI.

Nothing blocks a clean five-minute demo of both scenarios. Two things should be
done in the ten minutes before it, neither of which is a defect:

1. **`docker compose down -v && docker compose up -d`** to get the pristine
   seeded dataset, for the reasons above.
2. Confirm the worker is up and Mailhog is reachable at `:8025` - the bulk-email
   step is the one part of the demo that depends on a second process, and the
   `broker.py` fix in this commit is what keeps that process alive.

### Next work

Phase 8 (offline sync, PS §5.3) is unblocked: this gate is closed. Register
exactly `attendance` (check-in/out creation only) and `time_off_request`
(creation only) in `app/services/sync_entities.py`, and wire the frontend's
existing dormant `offline-db.ts` / `useOfflineMutation` / `OfflineBanner` onto
those two flows. Do not register any payroll-adjacent entity, and do not build
new sync infrastructure - the engine, the `sync_mutations` idempotency table and
the IndexedDB outbox are all already present and tested.

---

## 2026-09-05 — Phase 8: offline attendance and leave sync (PS §5.3)

### Gate check, and where this branch is based

Phase 7's gate is **closed** — re-read from its own final report, immediately
above: 349 backend tests passing, 395/395 RBAC cells matching Architecture §5,
both PRD §7 scenarios run end to end with no manual DB edits, 29/29 browser
checks, both required failure modes surfacing in the UI. Phase 8 was therefore
started rather than stopped.

**Branch base, stated because it is a deliberate departure from the usual
protocol.** `origin/dev` is still `cd823a17799c7d651066a51053e8db3e296ff3a2` —
neither Phase 5 nor Phase 7 has been merged into it. Branching `phase-8-offline`
from `origin/dev` would have put this work on a tree where the gate it depends
on does not exist, which is the same mistake `phase-6-dashboard` made when it
forked from `phase-3` (see "Phase 6 integrated with Phase 4" above). So:

```
origin/dev             cd823a1
phase-7-closeout       84e8c0f   (cd823a1 + phase-5 + phase-7-prep + closeout)
phase-8-offline        84e8c0f   <- branched here
```

Both branches are pushed. Merging them into `dev` in order — `phase-7-closeout`
first, then `phase-8-offline` — is a fast-forward.

### What was registered

Exactly two entities in `app/services/sync_entities.py`, per Architecture §8.3,
and both **CREATE-only**:

| Entity | Syncable | Never syncable |
|---|---|---|
| `attendance` | check-in / check-out **creation** | corrections, deletion |
| `time_off_request` | **submission** | approval, refusal, edit, deletion |

No payroll-adjacent entity is registered, and
`test_sync_registry_holds_exactly_the_two_phase_8_entities` asserts the **whole
set** rather than membership — adding a third has to break a test and be argued
for, rather than slipping in.

### Registration was not one line each, and that is the finding

The dormant module predicted that "registering an entity is one
`register_syncable_entity(...)` call in this file and nothing else". That was
true of AssetFlow's `note` and `asset`, which had no domain rules. It is false
for these two, and the difference is the substance of this phase.

Left as it was, the generic engine would have:

1. **Written rows with no authorization.** `_apply_create` built
   `model(**payload)` and called `BaseService.create`. The real method,
   `AttendanceService.create_attendance`, calls `employee_for(...)` — which is
   what enforces §5's "Attendance (own)". Through the generic path **an
   Employee could have posted attendance for anybody in the company.**
2. **Written rows with no derivation.** `worked_hours` and `status` are
   computed by `AttendanceService._compute`; leave `duration` by
   `request_duration`. The generic path would have stored whatever the client
   sent, or NULL.
3. **Served every row to everyone.** `SyncService.pull` filtered by tenant and
   cursor only. With no per-entity scoping, **any authenticated login pulling
   `attendance` would have received the whole organisation's attendance.**
4. **Accepted UPDATE and DELETE.** An attendance correction *is* an UPDATE.
   `AttendanceService.correct` guards it with `require_hr`; the generic UPDATE
   path does not. The correction gate would have been bypassed simply by
   choosing a different verb.

Every one of those is a *second, looser path to the database*, which
Architecture §9 forbids in as many words. Registering the entities without
addressing them would have satisfied the letter of "register two entities" and
broken the architecture the registration exists inside.

### What changed to fix it, and what deliberately did not

`SyncableEntity` gained **three optional fields**, each defaulting to the
behaviour the engine already had, so no existing registration changes meaning:

- `allowed_ops` — which operations this entity accepts. Both new entities are
  `frozenset({"CREATE"})`. `push` rejects anything else with
  `OPERATION_NOT_SYNCABLE` before the apply path runs at all.
- `create_handler` — applies a CREATE through the entity's **own service
  method** with the authenticated principal. Same method, same gate, same
  derivation and same audit row the REST router reaches.
- `scope_filter` — extra WHERE criteria for pull. HR sees everything; an
  Employee sees rows whose `employee_id` matches the **signed** claim; a login
  with neither gets `false()` — no rows rather than all rows, because the
  direction a scoping bug fails in matters more than how likely it is.

`SyncService.push` now takes the `CurrentUser` rather than just an email
(`actor_key` is still the email, so the idempotency key means exactly what it
did), `pull` takes the user and **refuses to serve an entity that registers a
scope filter without one** rather than silently serving it unscoped, and
`_apply_one_guarded` catches `HTTPException` so a domain service's 403/409/422
becomes a per-mutation `rejected` outcome instead of a 500 that would abort the
whole batch.

**Untouched:** the cursor and its safety lag, savepoint-per-mutation, the
`sync_mutations` idempotency table and replay, the conflict envelope, the audit
write, both HTTP routes, and the entire frontend sync engine. No new sync
infrastructure was built; what changed is that a registration can now state the
rules its entity already has.

### Idempotency — the property PRD §7 actually measures

Verified twice, in two different ways.

**In PostgreSQL** (`test_retried_mutation_id_creates_exactly_one_row`): the same
`client_mutation_id` pushed three times — twice alone and once inside a batch
alongside an unrelated mutation — produces **exactly one row**, counted with
`SELECT count(*)`, not inferred from the response. The replay also returns the
**first result verbatim**, so a client that never saw the original response
reconciles against the id it would have had, rather than being told its row does
not exist.

`test_idempotency_is_scoped_to_the_actor` pushes the *same* id as two different
people and gets two rows, because the key is `(actor_key, client_mutation_id)` —
a key that ignored the actor would let one person's retry return another
person's result.

**In a browser**, across a real kill-network cycle — see the demo below.

### The conflict edge case, documented

`docs/offline-sync-conflicts.md`. The short version: **the version-conflict path
is unreachable for both registered entities**, by construction rather than by
luck. A `conflict` outcome is produced in exactly one place, and two facts put
both entities on the other side of it — a CREATE has no `known_version` to
disagree with, and neither entity accepts UPDATE or DELETE. `ConflictModal`
therefore never opens on their account.

The document says why the machinery is kept anyway (it is what an UPDATE-capable
registration would need, and widening `allowed_ops` is now the explicit decision
that makes it live), why "corrections stay online-only" is the right cut rather
than a shortcut (`Keep Mine` on an attendance correction means "the device that
was offline longest wins", which is exactly backwards), and the six edge cases
that **are** reachable: retried mutations, terminal rejections, two check-ins in
one day, device clock skew, a stale leave balance, and an auto-approving leave
type that debits at reconnect.

### Frontend wiring — the dormant Phase 0 machinery, switched on

No new client infrastructure. `SYNCED_ENTITY_TYPES` goes from `[]` to the two
registered types, and two flows move onto the existing outbox:

- **Employee check-in and own attendance entry** → `useOfflineMutation.create`.
  HR entry for someone else, corrections and deletes stay on the REST path.
- **Employee leave-request submission** → the same, for `time_off_request`.
  Approval and refusal stay on REST.

`OfflineBanner` and `ConflictModal` were already mounted in `AppShell` and
needed no change. Added: `usePendingOfflineMutations`, a read over the existing
outbox store, and two "N waiting to sync" panels — because a check-in recorded
with no network has to appear *somewhere*, or the person taps the button again
and the honest answer to "did that save?" is a shrug.

**The clock trade-off, stated rather than buried.** An offline check-in is
timestamped by the **device**: the check-in happened when the person arrived,
not when their phone found a signal. Following the hook's existing design, the
employee check-in always goes through the outbox — online or offline — so there
is one code path rather than a fast REST path and a parallel offline queue. The
cost is that an online check-in is now client-timestamped too. The server still
authorizes it and still derives `worked_hours` and `status`, and PRD §8 already
states attendance is manually entered and corrected with no hardware
attestation, so this is consistent with the product's assumptions — but it is a
change from the server-clocked `POST /attendance/check-in`, and worth knowing.

### The offline demo scenario, run end to end

`harmonix360/frontend/scripts/offline-demo.mjs`, in a real Chromium with the
network cut at the **browser** (Playwright offline mode), so the app takes the
same path a phone in a basement would. **11/11 checks passed, 0 page errors.**

```
server rows before: attendance=9, requests=4
PASS  OfflineBanner appears once the app really cannot reach the server
PASS  Check-in is accepted offline and shown as waiting to sync
PASS  Nothing reached the server while offline — server still 9
PASS  Leave request queued offline
PASS  OfflineBanner clears on reconnect
PASS  Outbox drained after reconnect
PASS  Exactly one attendance row reached the server — no duplicate — 9 -> 10
PASS  Exactly one time-off request reached the server — no duplicate — 4 -> 5
PASS  Re-running sync creates nothing further — still 10
```

Row counts are read back from the API afterwards, so a duplicate would show up
as a **row**, not merely as a missing banner.

### Two limitations the demo found, and what was done about each

**A hard page load while offline fails.** There is no service worker, so the app
shell is not cached: in-app navigation works offline and every loaded screen
keeps working, but pressing reload with no signal gets the browser's error page.
That is the difference between an offline-capable data layer, which this is, and
an installable PWA, which this is not. A service worker is not sync
infrastructure and has its own failure modes — a stale shell served to someone
who has just been given a fix is worse than an error page — so it is on the
roadmap, and **the demo asserts the current behaviour explicitly** so that the
day someone adds one, the assertion fails and the limitation is removed
deliberately.

**The leave-request form was unusable offline.** Its leave-type dropdown is a
live read (`/time-off-types/lookup`), so with no network it was empty and "you
may submit a request offline" was false in the only way that matters. Fixed with
`useOfflineReferenceList`, which caches the lookup into the **same IndexedDB
store the sync engine already uses**, under its own key, written when the online
read succeeds and read back when it fails. It is **not** a third syncable
entity: `SYNCED_ENTITY_TYPES` still names exactly two and `/sync/pull` is never
asked for it. Leave types are configuration, not somebody's records — there is
nothing to conflict, reconcile or push.

That fix needed a second pass. Requesting the list only from the *form* was not
enough: the form mounts on demand, so someone who had never opened it online had
nothing cached at exactly the moment it mattered. The page requests it too, on
the same query key, so simply visiting Time Off while online warms the cache.

### Files created

- `harmonix360/backend/tests/test_offline_sync.py` — 13 tests.
- `harmonix360/frontend/scripts/offline-demo.mjs` — the browser demo above.
- `docs/offline-sync-conflicts.md` — conflict analysis and edge cases.

### Files modified

- `app/services/sync_entities.py` — the two registrations (was empty).
- `app/services/sync_registry.py` — `allowed_ops`, `create_handler`,
  `scope_filter`, all optional and all defaulting to previous behaviour.
- `app/services/sync.py` — honours those three; `push`/`pull` take the
  principal; `HTTPException` becomes a per-mutation rejection.
- `app/api/v1/routers/sync.py` — passes `current_user` through.
- `tests/test_platform_layer.py` — the registry test, see below.
- Frontend: `src/lib/sync-engine.ts` (the two entity types),
  `src/hooks/useOfflineMutation.ts` (`usePendingOfflineMutations`,
  `useOfflineReferenceList`, and an invalidation fix),
  `src/routes/attendance/AttendancePage.tsx`,
  `src/routes/time-off/TimeOffPage.tsx`.
- `ROADMAP.md`, `progress.md`.

### The test that changed, and why it is stronger

`test_sync_registry_is_empty_until_phase_8` asserted `registered_entity_types()
== []`. Its own docstring anticipated this phase, naming the two entities that
would replace the empty list. It is now
`test_sync_registry_holds_exactly_the_two_phase_8_entities`, and it asserts the
**whole set** plus an explicit disjointness check against ten forbidden entity
names — payruns, payslips, payslip lines, salary rules and structures,
contracts, employees, allocations, types and users. The old test could only
catch "something was registered"; this one catches "the wrong thing was
registered", which is the failure that would actually matter.

One frontend bug was found and fixed by the demo rather than by review:
`useOfflineMutation`'s `invalidate()` refreshed the entity cache but not the
outbox view, so a mutation queued with no network sat there invisibly —
`usePendingOfflineMutations` reads IndexedDB with `staleTime: Infinity` and
nothing else would ever have made it re-read.

### Validation record

- **Backend suite: 362 passed**, zero failures or skips, in ~3 minutes, on a
  freshly created database migrated to `019_payslip_delivery` and seeded. That
  is Phase 7's 349 plus this phase's 13.
- **RBAC audit: 395/395 cells still match Architecture §5.** Re-run because
  `pull` and `push` changed signature and the sync routes are in the audit.
- **Offline demo: 11/11 checks, 0 page errors**, across a real
  kill-network → mutate → reconnect cycle in Chromium.
- **Frontend: `npm run build` passes** `tsc -b` and the Vite production build.
  The pre-existing large-bundle advisory remains.
- Same container-based procedure as Phase 7 (see its environment notes):
  WeasyPrint's native libraries mean the suite must run inside
  `peoplepay360_backend`, and `pip install -r requirements-dev.txt` does not
  survive a container recreate.

### Follow-up found while writing the run instructions: /health was not proxied

Stopping the backend container did **not** put the app into offline mode when it
was served by the Docker frontend, and this is worth recording because of the
shape of the bug rather than its size.

`reachability.ts` pings `/health`, which sits outside the `/api` prefix.
`vite.config.ts` has a second proxy entry for exactly that reason, with a
comment saying so. `nginx.conf` never got the matching entry — so `try_files`
answered `/health` with `index.html` and a **200**, `res.ok` was true, and the
app cheerfully reported itself online with the backend stopped. The offline
banner worked under the dev server and not in the container: the environment
you test on and the one you ship diverged, which is the worst place for a
difference to hide.

`nginx.conf` now proxies `location = /health` to the backend. With the backend
stopped nginx answers 504, which is a falsy `res.ok` and the honest answer,
while `GET /` still serves the app shell so the SPA keeps running.

Verified in a browser against the **nginx build** (127.0.0.1:3000, not
localhost:3000 — see below), driving the cycle with `docker stop/start
peoplepay360_backend` rather than Playwright's offline mode: banner appears on
stop, check-in queues, banner clears on start, outbox drains, and exactly one
row reaches the server. 6/6.

**A correction to this session's Phase 7 notes.** A Vite dev server was running
on port 3000 throughout, bound to `::1`, while the Docker frontend was bound to
`::`. On Windows `localhost` resolves to `::1` first, so every browser check in
this session that used `http://localhost:3000` was served by **Vite from
source**, not by the container's built bundle. The behaviour those checks
verified is real — same application code, same backend, same database — but the
Phase 7 note crediting the container rebuild for making "Print Payslip" appear
is wrong: the merged source was what the browser saw. The rebuild was still
needed for the containerised deployment, which is what this `/health` fix was
found in. Use `127.0.0.1:3000` to reach the container and `localhost:3000` to
reach the dev server, or stop one of them.

### Next work

Phase 9 (AI and MCP) and Phase 10 (realtime, observability) — both P2, both
dormant, both described concretely in `ROADMAP.md` §8 and §9. The invariant that
governs Phase 9 is worth repeating here because it is the one that must not
slip: **AI is architecturally incapable of writing to `Payslip`/`PayslipLine`**,
and an MCP session is bound to an authenticated user's role, wrapping the same
`require_role`-guarded service methods the REST routers call.

Before either: merge `phase-7-closeout` and then `phase-8-offline` into `dev`.
Both are fast-forwards, and until that happens `origin/dev` does not contain
Phase 5, Phase 7 or Phase 8.

---

## 2026-09-05 — Phase 9: AI context layer and MCP tools (PS §5.1/§5.2)

### Gate check, and where this branch is based

Fetched origin and read `origin/dev:progress.md` before touching anything.
**`origin/dev` is still `cd823a17799c7d651066a51053e8db3e296ff3a2`** and does
not contain Phase 5, 7 or 8 — the merge the Phase 8 handoff recommended has not
happened. Its progress.md and its code agree with each other; **no discrepancy
was found** between them.

The protocol says to branch from the `origin/dev` tip. **This branch is cut from
`origin/phase-8-offline` (`f4c18e7`) instead**, and that is a deliberate,
reported deviation:

- Phase 9's own brief requires Phase 7's gate to be confirmed before starting,
  and that gate's artifacts (`docs/rbac-audit.md`, `scripts/demo_scenarios.py`,
  the seeded paid payrun with a real PDF) exist only on `phase-7-closeout`.
- Phase 10's brief requires an **offline-sync OTel trace**, and offline sync
  exists only on `phase-8-offline`. On `cd823a1` that requirement is not merely
  harder, it is unsatisfiable.
- `phase-8-offline` contains `cd823a1` as an ancestor and contains all of
  `phase-7-closeout`, so this is still "on top of the current integration
  state" — it is the integration branch that is behind, not this one.

**Phase 7's gate was verified against code, not just read.** Every artifact its
close-out claims is present: the RBAC audit and its reproducible script, the
demo-scenario script, `ROADMAP.md`, `_seed_paid_payrun`/`_verify_pdf`, the
`broker.py` `socket_timeout=None` fix, migrations through `019_payslip_delivery`,
and `sync_entities.py` registering exactly two CREATE-only entities. The
documented suite total was reproduced exactly: **362 passed** on a freshly
created, migrated and seeded database, before a line of Phase 9 was written.

### The shape of the thing, and what it is not

This is not "send the payslip to an LLM and ask it to summarize it". The
deliverable is a context layer that ORCHESTRATES existing read services so the
model can reason across connected records, with the deterministic engine
remaining the sole authority for every figure.

```
existing services  ->  context_builder  ->  prompts  ->  provider_router
   (authoritative)      (task-scoped)      (framed)      (narration only)
```

**Not one line of payroll mathematics was written.** `app/ai/` and `app/mcp/`
contain no rule evaluation, no Payslip construction and no call into the compute
engine — asserted by a test that scans both trees for those tokens.

### 1. Read services the context layer orchestrates (new, read-only)

- **`app/services/contract_history.py`** — contract timeline with the wage
  change between each consecutive pair; period-to-contract resolution that
  DELEGATES to `payroll_context.resolve_period_contract` rather than
  reimplementing it (a second "which contract applies" that agreed 99% of the
  time would show a Time Machine contract the employee was not paid under);
  expiring contracts; and an overlap check that should always return zero
  because the EXCLUDE constraint makes overlaps unwritable.
- **`app/services/pay_comparison.py`** — period-over-period payslip diff.
  Inputs come from `Payslip.context_snapshot` (frozen at compute, migration
  018), never from today's contract: explaining a July payslip with August's
  wage is the exact bug 018 exists to prevent. A missing value is reported as
  unknown rather than defaulted to zero — "we do not know last month's LOP" and
  "last month's LOP was zero" are different facts and only one supports the
  sentence "the LOP is what changed".
- **`app/services/payslip_explain.py`** — the calculation tree: frozen inputs,
  lines in the sequence they ran, category subtotals, and the persisted totals.
  The line's own copied `code`/`name`/`category` are authoritative; anything
  read from the live `SalaryRule` is nested under `rule_definition_now` and
  labelled as current, so an explanation cannot describe a formula that never
  produced the number beside it.

### 2. `app/ai/context_builder.py` — the AI context layer

Five task-scoped builders: payslip explanation, department variance, payroll
blockers, anomalies, and general employee context. Each returns an `AIContext`
carrying `facts`, `unavailable` and `sources`.

Four properties, each enforced rather than intended:

1. **Task-specific.** A payslip question loads that employee's two payslips,
   contracts, attendance and leave — not the department's payroll.
2. **Traceable.** `sources` names the service behind each section.
3. **Explicit about absence.** `unavailable` carries a plain-English reason, and
   the prompt tells the model those are the things it may not guess at. Absence
   reaching the model as a fact rather than a missing key is what lets it say
   "the data does not show why".
4. **Money as strings.** No `float()` anywhere. A test walks the entire rendered
   payload and fails on any float found.

`deterministic_signals()` prefers Phase 10's anomaly engine when present and
falls back to the dashboard's alert queries when it is not, **reporting which
source it used into the prompt** — so an answer built on the fallback is
traceable as such rather than silently degraded.

### 3. MCP: 19 tools, every one calling an existing service

`app/mcp/tools.py` holds the bodies as plain async functions taking a session;
`app/mcp/server.py` registers thin schema shims. That split is why "MCP and REST
share one path to the database" is a test rather than a comment — the bodies are
callable without starting the MCP process.

All 13 read tools and all 5 action tools from Architecture §8.2, plus the
retained `query_audit_trail`. **`compute` is deliberately absent**: it is the
only write path to `Payslip`/`PayslipLine`, and a test asserts the action set
contains nothing matching "compute" or "payslip".

**RBAC is not reimplemented.** `app/mcp/identity.py` resolves `actor_email` to a
real `User` row and calls `app.api.v1.deps.require_role` — the same function
object the routers use. There is deliberately **no demo-admin fallback**: an
unauthenticated HTTP request is treated as the demo admin outside production,
and doing that for MCP would hand admin rights to any agent that named no actor.

### 4. Propose -> human confirm -> execute

`app/ai/proposals.py`. The model's output can only ever become a PROPOSAL;
`execute()` runs only when a human confirms that specific proposal by id, and
runs it **unmodified** — an "improved" version of a confirmed action is an
unconfirmed action. Redis holds the pending proposal for 15 minutes; the durable
record is the audit log (`AI_PROPOSED_ACTION` -> `AI_CONFIRMED_ACTION` ->
`AI_ACTION_EXECUTED`), so there is no parallel audit system. A validation
failure inside the service is left to propagate unchanged and audited as
`AI_ACTION_REJECTED_BY_VALIDATION`. The proposable action set is closed to
exactly `create_time_off_request`.

### 5. Provider routing, retargeted

Groq -> Cerebras, unchanged mechanically. Three changes:

- **The prompt and response dumps to stdout were removed.** The previous build
  printed the full prompt and the full response, which put every explained
  payslip — names, wages, net pay — into `docker compose logs` and anything
  downstream of them. Only sizes and outcomes are recorded now.
- A system message and a low temperature, restating that the figures are final.
- The docstring now states the invariant: the provider is an inference engine,
  and `AIUnavailableError` is a clean state rather than an outage.

### 6. Verification — what was actually observed

**Full regression: 390 passed**, zero failures (362 inherited + 28 new in
`tests/test_ai_mcp.py`). Frontend `npm run build` passes `tsc -b` and Vite.
`ruff check app/ --select F,E9,B` reports only the 7 pre-existing findings in
files this branch did not touch.

**MCP over the real Streamable HTTP transport** (server started as its own
process, exercised with a real `fastmcp` client):

```
tools over the wire: 19
wrong api key          -> {"status":"error","error":"Invalid or missing MCP agent API key"}
HR Manager actor       -> 403: Role 'hr_manager' is not authorized ... requires
                          ['admin','hr_payroll_manager','hr_payroll_user']
payroll manager actor  -> total_net_salary_paid 274800.00 over 5 payslips
```

The middle line is the point: a VALID agent key does not widen what the actor
may do. PRD §7's advanced metric — the same mutation tool refused for an
unauthorized role and accepted for an authorized one — was verified on
`approve_time_off_request`: EMPLOYEE actor got `403: HR Manager or above
required`, HR Manager actor approved it, and the audit row records
`APPROVE_TIME_OFF_REQUEST` by the impersonated user.

**`scripts/ai_demo.py` — all three demos pass end to end** over real HTTP
against a running stack (API + Taskiq worker + Postgres + Redis), with
`ENVIRONMENT=production` so the demo-admin fallback was off and every call
carried a real login.

DEMO 1 asked "why did Lakshmi's salary change this month?" about a freshly
computed August payrun, and the context reached past the payslip on its own:

```
net 41800.00 (July) -> 37514.29 (August), delta -4285.71
WORKED_DAYS       23.00 -> 18.00
UNPAID_LEAVE_DAYS  0.00 ->  3.00
LOP_AMOUNT         0.00 -> 4285.71
CONTRACT_WAGE  30000.00 -> 30000.00   (unchanged — NOT a pay cut)
GROSS_AMOUNT   42000.00 -> 42000.00   (unchanged)
```

Eight fact sources contributed, named in the response. That gross and wage are
flat while net moved is what makes the answer specific rather than plausible.

DEMO 3 verified the whole mutation chain: proposing wrote **nothing** to the
domain (request count unchanged), the human confirmed, exactly one record was
created with status `to_approve`, a replayed confirmation returned 404, and the
audit trail carried `AI_PROPOSED_ACTION by ai-agent`, `AI_CONFIRMED_ACTION by
employee@peoplepay360.com` and `AI_ACTION_EXECUTED`.

### 7. The limitation that matters, stated plainly

**No `GROQ_API_KEY` or `CEREBRAS_API_KEY` exists in this environment**, so the
narration step could not be exercised against a real provider. Both demos
reported `ai_unavailable` at that step — which is the designed behaviour and was
itself verified — and printed the full authoritative fact set that would have
been narrated.

What this means precisely:

- **Verified end to end:** context assembly, prompt rendering, RBAC at both the
  route and the worker, the queue/poll cycle, the propose/confirm/execute chain,
  the audit trail, and the clean-unavailable state.
- **Verified with a stubbed provider** (`tests/test_ai_mcp.py`): the prompt the
  model actually receives contains the ERP's exact figures, the employee's name,
  the `UNAVAILABLE INFORMATION` block and the instruction never to recalculate;
  the response is threaded back with its provider recorded.
- **Not verified:** the quality of a real model's prose. Set either key and the
  same code path narrates.

### Files created

- `harmonix360/backend/app/ai/context_builder.py`, `context_assembly.py`,
  `proposals.py`, `prompts/__init__.py`
- `harmonix360/backend/app/mcp/tools.py`, `identity.py`
- `harmonix360/backend/app/services/contract_history.py`, `pay_comparison.py`,
  `payslip_explain.py`
- `harmonix360/backend/tests/test_ai_mcp.py`, `scripts/ai_demo.py`
- `harmonix360/frontend/src/types/ai.ts`, `hooks/useAi.ts`,
  `routes/assistant/AssistantPage.tsx`

### Files modified

- `app/ai/provider_router.py` (retargeted; stdout prompt dumps removed)
- `app/api/v1/routers/ai.py` (rewritten: ask / proposals / confirm / reject)
- `app/jobs/tasks/ai_jobs.py` (added `run_hr_insight`, `run_ai_action_proposal`)
- `app/mcp/server.py` (the §8.2 tool set registered)
- `frontend/src/router.tsx`, `frontend/src/lib/navigation.ts`

No payroll service, model, migration, schema or router was modified. The
`git diff --stat` against `f4c18e7` touches nothing under `app/services/payroll*`,
`app/models/` or `alembic/`.

---

## 2026-09-05 — Phase 10: realtime, observability and the read-side intelligence layer (PS §5.4-§5.10)

### Gate check and base

Branched from `phase-9-ai-mcp` (`ab89b74`), not from `origin/dev` — same reason
Phase 9 gave, and now compounded: Phase 10's brief requires an **offline-sync
OTel trace**, and offline sync exists only from `phase-8-offline` onward. On
`cd823a1` that requirement is unsatisfiable. This branch therefore contains
Phases 5, 7, 8 and 9, and `origin/dev`'s tip is an ancestor of it.

Phase 7's gate was re-confirmed by Phase 9 against code, and Phase 9's own suite
(**390 passed**) was reproduced before a line of Phase 10 was written.

Phase 9's AI/MCP/context architecture is **preserved, not rewritten**. The one
place Phase 10 touches it is the hook Phase 9 deliberately left:
`context_builder.deterministic_signals` prefers `app/services/anomalies.py` when
it exists and reports which source it used. That branch now takes the engine.

### 1. Realtime — the commit-after-broadcast guarantee, made structural

`app/realtime/events.py`. Services call `queue_event(...)`, which only ever
appends to `session.info`. Dispatch happens in **exactly one place**:
`get_db` calls `dispatch_after_commit(session)` immediately after
`await session.commit()` returns. If the commit raises, control goes to the
`except` branch, the transaction rolls back, and the staged events die with it.

This is why it is not enough to "remember to broadcast after committing" in each
router. `get_db` commits AFTER the handler returns, so a `broadcast()` written
at the end of a handler body runs BEFORE that commit — and would announce a
check-in that a later constraint violation then erased. Putting the only send
site after the only commit site makes the wrong order unwritable rather than
merely discouraged.

Events, per Architecture §8.4: attendance check-in / check-out / correction, new
time-off request, approval or refusal outcome, payrun computed / validated /
paid, and bulk-email queued.

**A channel is not an audience.** `Subscriber` carries the principal that opened
the socket, and `events._visible_to` applies §5's matrix per frame: payroll
channel to payroll roles only (HR Manager included in the refusal), and an
Employee sees their own rows and nobody else's. Without it, an Employee
subscribed to `approvals` would receive every colleague's leave outcome — the
same disclosure a REST endpoint would be faulted for, travelling over a
WebSocket.

Every failure is swallowed and logged. A dead browser tab must never turn a
committed payroll transaction into a 500, and a test asserts exactly that.

### 2. Observability — three traces, and the removal of everything else

`payroll.compute`, `ai.mcp`, `offline.sync`. `telemetry._span` **refuses** any
other name with a `ValueError`, so a fourth trace cannot be added by accident.

The previous build auto-instrumented FastAPI, SQLAlchemy, Redis and HTTPX.
**That was removed.** Architecture §8.5 permits "three named traces only… no
instrumentation is added outside these three", and PRD §5.5 asks for "three
specific business traces (not a generic showcase)". A span per request and per
statement is not more observability — it buries the one trace the deliverable is
about. It also carries a cost this domain cannot ignore: a SQLAlchemy span
carries the statement, and statements here contain wages.

**Nothing sensitive enters a span.** No prompt, no completion, no amount, no
employee name. The payroll trace records public ids, counts and statuses; the AI
trace records task type, tool name, provider, latency and token counts. A test
captures the attributes a real compute sets and asserts none of them equals a
monetary value from the payslip.

### 3-7. The read-side features

All five are GETs. `test_phase_10_added_no_write_endpoint` asserts the insights
router's method set is exactly `{"GET"}`.

- **View Calculation (§5.6)** — `app/services/payslip_explain.py`, built in
  Phase 9 and exposed here. Frozen inputs, lines in the sequence they ran,
  category subtotals, persisted totals. The subtotals are shown ALONGSIDE the
  payslip's own gross and net, never instead of them; a test asserts every
  category subtotal equals the exact Decimal sum of its lines.
- **Pay-change comparison (§5.9)** — `pay_comparison.compare_with_previous`.
  Inputs come from `context_snapshot`, never from today's contract.
- **Contract Time Machine (§5.8)** — `contract_history`. The period-to-contract
  resolution **delegates to the payroll engine's own resolver**, and a test
  asserts the highlighted contract is the one in the payslip's
  `reference_snapshot`. A client-side date comparison would agree almost always,
  and the exception would be a screen confidently showing a contract the
  employee was not paid under.
- **Validation Firewall (§5.10)** — `app/services/firewall.py`, a read-side
  layer over `PayrunService.validation_report` adding grouping, employee names
  and a navigation target per issue. It computes no severity of its own and
  cannot clear a finding. **Revalidate is the EXISTING `POST /payruns/{id}/validate`**,
  named in the response rather than reimplemented. An unrecognised warning code
  falls through to a generic entry rather than being hidden — a blocking issue
  nobody can see still blocks.
- **Anomalies (§5.7)** — `app/services/anomalies.py`, seven deterministic
  checks. Every finding carries `current_value`, `baseline` and a `reason`
  quoting the threshold it crossed, so a reader can disagree with the threshold
  instead of having to trust the label. A detector that raises reports itself as
  a `detector_failed` finding rather than silently removing its category.

### Two anomaly-quality bugs found by running it, and fixed

The demo surfaced both; neither would have failed a test I had written first.

1. **`department_spend_spike` reported "payroll fell 100%" for every department
   in any month whose payroll had not been paid yet.** Department totals count
   PAID payslips, so an unrun month reads as zero everywhere. Fixed: if nothing
   at all was paid in the current period there is no spend to compare and the
   detector stays silent. If SOME departments were paid and one was not, that
   one's 100% fall is genuine and is still reported.
2. **`low_attendance` compared against the WHOLE period.** On the 3rd of the
   month everyone had "attended 2 of 22 days" and every employee in the company
   was flagged — every month, until the month ended. Fixed: the denominator and
   the numerator both stop at today. Days that have not happened are not
   absences.

Both have regression tests. Observed after the fix, against the seeded data:
August 2026 returns **0** findings (correct — nothing anomalous), and the
current month returns 5 real `low_attendance` findings reading "attended 0 of 4
scheduled days so far (0.00%)", because the seed creates attendance for July and
August only. That is PRD §7's "at least one real, non-fabricated warning".

### 8. Wiring the layers together

`deterministic_signals` now resolves to `anomalies.detect_all`, and says so in
the prompt's `fact_sources`. Verified live: the AI anomaly question reports
`anomalies.detect_all (deterministic Phase 10 anomaly engine)` rather than the
Phase 9 dashboard fallback.

### 9. UI

Real screens, no static data, no fake charts. `ViewCalculation` (with the
comparison tab) on the payslip dialog; `ContractTimeMachine` on the employee
detail page; `FirewallPanel` replacing the flat warning list on the payrun page;
a new Anomalies page with the live payroll feed indicator. Loading, empty,
error, AI-unavailable, realtime-unavailable and no-comparison states are all
handled explicitly — the empty anomaly state says all seven checks ran and none
fired, because that is a real answer.

`useRealtime` treats a frame as a HINT: it invalidates the relevant query keys
and lets the normal REST read produce the value. It never writes a frame's
payload into the cache. If frames became the source of the numbers on screen, a
dropped frame would leave a stale payroll figure that looked authoritative.

**Two proxy fixes without which realtime would have looked implemented and never
worked:** `vite.config.ts` needed `ws: true`, and `nginx.conf` needed
`proxy_http_version 1.1`, the `Upgrade`/`Connection` headers via a
`$connection_upgrade` map, and a long `proxy_read_timeout` (a WebSocket is idle
between events; the default 60s would drop every channel a minute after it
connected).

### 10. Verification — what was actually observed

**Full suite: 417 passed**, zero failures (390 inherited + 27 new). Frontend
`npm run build` passes `tsc -b` and Vite. `ruff check app/ tests/` reports only
the 9 pre-existing findings in files this branch did not touch.

`scripts/phase10_demo.py` — **all checks pass**, over real HTTP and a **real
WebSocket** against a running stack:

```
[PASS] HR Manager is refused on the payroll channel
[PASS] An unsigned token is refused
[PASS] A payrun.computed frame arrived over the socket — computed=1
[PASS] The payslips the frame announced are already committed and readable
                                          — 1 readable vs 1 announced
[PASS] Exactly three traces — payroll.compute, ai.mcp, offline.sync
[PASS] Frozen inputs present — CONTRACT_WAGE=30000.00, LOP_AMOUNT=4285.71,
                               UNPAID_LEAVE_DAYS=3.00, WORKED_DAYS=18.00
       lines: BASIC 30000.00, HRA 12000.00, GROSS 42000.00, PT 200.00,
              LOP 4285.71, NET 37514.29
[PASS] HR Manager is refused the calculation view
[PASS] A real change was detected — net 41800.00 -> 37514.29 (-4285.71)
[PASS] The period resolves to the contract the payslip was computed against
[PASS] Revalidate points at the EXISTING validate endpoint
[PASS] At least one REAL anomaly is surfaced against the seeded data
[PASS] The AI context uses the Phase 10 anomaly engine, not the fallback
```

The fourth line is the commit-after-broadcast guarantee observed end to end: not
"the frame came last", but "the rows the frame describes are readable through
the API at the moment it arrives".

**Payroll is unchanged.** `test_payroll_computation_is_unchanged_by_phase_10`
computes a hand-checkable payslip through the real endpoints with realtime and
tracing active — BASIC 30000.00, HRA 12000.00, GROSS 42000.00, PT 200.00, NET
41800.00, exact Decimal equality. The `git diff` of `app/services/payroll.py`
against `ab89b74` contains only `payroll_span(...)` wrappers and
`payroll_progress(...)`/`delivery_progress(...)` staging calls: no arithmetic, no
rule, no persisted column. Nothing under `app/models/` or `alembic/` changed —
**Phase 10 adds no migration.**

Phase 9's `scripts/ai_demo.py` re-run on this branch: all checks still pass.

### Environment note that cost two debugging cycles

A manually started `uvicorn`/`taskiq` in a container does **not** pick up edited
code (no `--reload`), and `pkill` is absent from the runtime image — so a
"restart" that silently failed left a stale process serving old code. It
presented first as a WebSocket 403 (Starlette closes an unmatched websocket
route, which uvicorn reports as 403) and later as the anomaly engine returning
findings the API no longer produced. Kill by PID from `/proc` and verify the new
routes appear in `openapi.json` before trusting a demo run.

### Remaining limitations

- **No AI provider key**, unchanged from Phase 9: narration still reports the
  clean `ai_unavailable` state. Everything up to and including the prompt is
  verified; the model's prose is not.
- **The WebSocket token travels in the query string**, because the browser
  `WebSocket` constructor cannot set headers. Query strings reach access and
  proxy logs more readily than headers do; production should pair this with a
  short-lived socket-scoped ticket rather than the session JWT.
- **`/realtime/status` counts this process's own connections.** With more than
  one API replica it is a local view, correct for "is my feed live" and wrong if
  read as cluster-wide.
- **Realtime broadcasts are in-process.** A multi-replica deployment needs a
  Redis pub/sub fan-out; the send site is a single function, so that is a
  contained change.
- **The WebSocket route is not covered by an in-suite test**, because a real
  socket needs a second event loop and the async fixtures are bound to this one.
  It is covered by `scripts/phase10_demo.py` against a running server, which is
  where a proxy or upgrade-header misconfiguration would actually show up.

### Files created

- `app/realtime/events.py`, `app/api/v1/routers/realtime.py`
- `app/api/v1/routers/insights.py`, `app/services/anomalies.py`,
  `app/services/firewall.py`
- `tests/test_phase10_insights.py`, `scripts/phase10_demo.py`
- `frontend/src/types/insights.ts`, `hooks/useInsights.ts`, `hooks/useRealtime.ts`,
  `components/insights/{ViewCalculation,FirewallPanel,ContractTimeMachine}.tsx`,
  `routes/insights/AnomaliesPage.tsx`

### Files modified

- `app/core/telemetry.py` (three traces; auto-instrumentation removed),
  `app/main.py` (routers mounted; `FastAPIInstrumentor` removed)
- `app/core/database.py` (the single post-commit dispatch site)
- `app/realtime/ws_manager.py` (per-subscriber visibility)
- `app/services/{attendance,time_off,payroll,sync}.py` — event staging and trace
  spans only
- `app/ai/context_assembly.py`, `app/ai/provider_router.py`,
  `app/mcp/tool_wrapper.py` — retargeted onto the `ai.mcp` trace
- `frontend/vite.config.ts`, `frontend/nginx.conf` — WebSocket proxying
- `frontend/src/router.tsx`, `lib/navigation.ts`, and the payslip, payrun and
  employee pages

---

## 2026-09-05 — Adversarial / failure-mode regression suite

### Starting point and protocol

- Fetched `origin`, read `origin/dev:progress.md`, and diffed its claims
  against the code before writing anything. Base commit:
  `cd823a17799c7d651066a51053e8db3e296ff3a2`. Every claim checked held:
  `LOP_AMOUNT` in `SEED_CONTEXT_NAMES`, `lop_schedule_unavailable` emitted as
  blocking, migration 018's three nullable JSONB columns, the seed creating no
  payroll rows, and the suite at 339.
- Branch `phase-11-prefix-tests`, cut from that tip. **Testing only** — no
  production module was modified, and no feature behaviour was added or
  changed.
- Baseline before the new file, on a freshly created database migrated to 018
  and seeded: **339 passed**.

### What was added

One file: `harmonix360/backend/tests/test_adversarial_failure_modes.py`,
**14 collected cases** covering the eight requested scenarios (six of the
fourteen are the parametrized invalid-formula shapes, and one is a second,
lower-level assertion on the LOP edge case).

Every case asserts the same three properties, through shared helpers
(`assert_controlled`, `assert_explainable`) so the bar is enforced by code
rather than described in prose:

1. **Controlled** — a specific deliberate status code, and an explicit
   `!= 500` assertion with its own message. A failure mode that 500s is
   uncontrolled by definition.
2. **Explainable** — the body must mention named terms a reader can act on
   (`version`, `balance`, `missing_bank_details`, the offending rule code…).
   An empty or generic body fails even when the status is right.
3. **Uncorrupted** — the database is inspected DIRECTLY afterwards, past the
   API's own filters, to prove the rejected operation wrote nothing. This is
   the assertion that distinguishes the suite from the happy-path coverage
   that already exists: a rejection that still committed a row would otherwise
   read as a pass.

### Results — 8 of 8 controlled and explainable, no bugs found

| # | Scenario | Result | What was actually observed |
|---|---|---|---|
| 1 | Duplicate payrun compute | **PASS** | Stale-version replay → 409 naming `version`, no payslip written. Three computes leave exactly 1 payslip and a constant line count; zero orphaned `payslip_lines` |
| 2 | Concurrent approval race on one allocation | **PASS** | Both approvals held at a barrier after reading the SAME allocation version (asserted). One approves, one 409s telling the user to refresh; neither crashes. `taken` is exactly 1.00 and never exceeds `allocated` |
| 3 | Overlapping / invalid contract | **PASS** | Mid-range overlap → 409; single shared day (the inclusive `'[]'` bound) → 409; inverted dates → 422. Contract row count unchanged after all three. A contract starting the day AFTER still succeeds |
| 4 | Missing bank details | **PASS** | Computes (does not block the whole run), blocking warning naming the employee, Validate → 409. Fixing the record alone does NOT unblock; only fix + recompute does |
| 5 | Insufficient leave balance | **PASS** | 409 mentioning balance; allocation `taken` still 0.00 and its `version` unchanged — no partial debit; request stays `to_approve`. A fitting request still approves |
| 6 | Invalid salary formula | **PASS** (6 shapes) | Injection shapes (`__import__(...)`, attribute access, subscript, malformed) → 422 at RULE-save. Forward reference and unknown name → 400 at STRUCTURE-save, naming the offending rule code. None reached a payrun |
| 7 | Mutation of a PAID payslip | **PASS** | Recompute, rename, delete-payrun and delete-payslip each → 409 with an explanation. Gross, net, worked_days, status, version and line count re-read from the database are byte-identical afterwards; `deleted_at` still NULL |
| 8 | LOP zero working days | **PASS** | See below |

**No scenario produced an uncontrolled or unexplainable result, so nothing is
escalated to the team from this suite.**

### Scenario 8 in detail, since it was the one most likely to divide by zero

Attacked with a schedule that is real and non-empty but has **no day inside
the period** — a Sunday-only schedule over Monday 2026-09-07 to Saturday
2026-09-12. That is nastier than "no schedule at all", because every naive
guard of the form `if schedule is None` passes it straight through to the
division.

Observed, verbatim from the API:

```
COMPUTE -> 200
  computed_count: 0
  skipped: [{"employee_name": "Zero Days", "reason": "Zero Days has zero or
     undetermined scheduled working days in this period. LOP_AMOUNT cannot be
     calculated; fix the schedule and recompute."}]
  blocking_issues: {"lop_schedule_unavailable": 1}

VALIDATE -> 409
  blocking_by_code: {"lop_schedule_unavailable": 1, "no_payslip": 1}
  references: ["emp_…", "ctr_…"]

payslips in run: 0
```

No `ZeroDivisionError`, no 500, and — the part that matters most — **no
payslip built on a fabricated LOP**. A second case (`test_8b`) asserts the
same edge one level down against `build_payroll_context` directly: with zero
working days `LOP_AMOUNT` is **absent from the seed dict entirely**, not zero
and not `None`, either of which a downstream formula would have consumed
silently. `CONTRACT_WAGE` is still present, so the failure is scoped to LOP
rather than poisoning the whole context.

### One thing the team should know (not a bug, but it will bite again)

`test_dashboard.py::test_attendance_health_is_schedule_derived_not_hardcoded`
asserts `expected_working_days == 15` against an **org-wide, unfiltered**
aggregate. It therefore fails whenever the database contains any active
employee with a working schedule beyond its own fixture — regardless of
whether that employee arrived from the seed, another suite, or a manual probe.

It was seen failing once during this work with `21 == 15`, traced precisely to
two rows left in the shared test database by other activity (a seeded demo
employee on a Mon–Fri schedule contributing 5, and a manual probe employee on
a Sunday-only schedule contributing 1, because 2026-09-13 is a Sunday). **The
adversarial suite itself leaks nothing** — employee-with-schedule counts were
measured before and after a full run of it and were identical.

This is the same over-broad-assertion pattern that was corrected in three
sibling tests during the Phase 4/6 integration. It is a test-isolation issue,
not a dashboard defect: the query counted the employees because the employees
existed. The `phase-7-prep` branch scopes this test by department the same way
the siblings were scoped; that fix is deliberately NOT duplicated here,
because this branch is testing-only.

Practical consequence for whoever runs the suite next: **use a dedicated,
freshly migrated and seeded database**, as the gap-fix handoff already
advises. On a shared or long-lived database this one assertion is a false
alarm waiting to happen.

### Validation record

- Full suite on a purpose-created database `peoplepay360_p11_tests`, migrated
  to `018_payroll_snapshots` and seeded: **353 passed** in 107 s, zero
  failures, zero skips (339 inherited + 14 new).
- Baseline on the same clean database before the new file: 339 passed.
- The new file alone: 14 passed.
- `ruff check tests/test_adversarial_failure_modes.py --select F,E9,B`
  (B008 excluded, matching the project's convention): all checks passed.
- The zero-working-days behaviour was additionally observed through a manual
  HTTP probe, quoted above, rather than only through the test's own
  expectations. Its scratch employee was removed from the shared test database
  afterwards.

### Files

- Created `harmonix360/backend/tests/test_adversarial_failure_modes.py`.
- **Nothing else.** No application module, migration, schema, router, service,
  dashboard, PDF or email file was touched on this branch — verifiable with
  `git diff --stat cd823a1..phase-11-prefix-tests`.

---

## 2026-09-06 — Closing the two verification gaps left open by final-integration

Branch `verify/advanced-profile-and-ai-keys`, cut from `origin/main` (decc748).
Both gaps that the integration report listed as unverified are now closed, and
both are **PASS** — but closing the second one uncovered a defect that would
have silently disabled the entire AI layer during the eval.

### Gap 1 — the advanced profile, built genuinely from scratch: PASS

The integration session verified the core profile against a warm, bind-mounted
container. That proves nothing about the image. So:

1. `docker compose --profile advanced down` (containers only — the `pgdata`
   volume was deliberately **kept**, because wiping it destroys the hand-made
   demo rows progress.md already documents, and nobody asked for that).
2. `docker rmi` on all four project images. `odoo2026-mcp-server` was **13
   hours old**, i.e. built before Phase 9 existed — exactly the staleness this
   check was meant to catch.
3. `docker compose --profile advanced build --no-cache` — a full rebuild with
   no layer reuse, exit 0.
4. `docker compose --profile advanced up -d --force-recreate` — all 8 services
   up, backend healthy, `peoplepay360_mcp_server` listening on :8100.

The fresh image imports `fastmcp`, `groq`, `cerebras.cloud.sdk`, `weasyprint`
and `simpleeval`, so nothing Phase 5/9 added is missing from a clean build.

`scripts/verify_advanced_profile.py` (added here) then drove the MCP server over
a **real Streamable HTTP JSON-RPC handshake**, not a port check:

- `initialize` → 200, server named itself, session id issued
- `tools/list` → **19 tools**, and `compute` is absent — the AI still has no
  write path to the rule engine
- `get_employee` over MCP returned the real seeded employee
- **a valid agent key acting for an HR Manager still gets
  `403: Role 'hr_manager' is not authorized`** on `create_payrun` — the key
  authenticates the process, it never widens the actor
- a wrong `api_key` is refused

A bare `GET /mcp` answers **406**, not the 400 the CI comment predicts. CI only
checks that curl connects, so the job still passes, but the comment is now
inaccurate for this FastMCP version (3.4.6).

Full suite re-run against the fresh image on a clean database: **431 passed**.

### Gap 2 — real model prose: PASS, after fixing a defect that blocked it

`GROQ_API_KEY` and `CEREBRAS_API_KEY` were present in `.env` this session and
**both authenticate** (`/v1/models` → 200 on each).

**The defect.** With valid keys, every AI request still failed. Both configured
default models had been **retired by their providers**:

```
GROQ     llama-3.1-8b-instant -> 404 model_not_found
CEREBRAS llama3.1-8b          -> 404
```

A 404 is a provider error, so the router marks the provider dead and falls
through the whole chain — meaning **every AI answer would have degraded to
"unavailable" during the eval, with valid keys in place and nothing in the UI
explaining why.** Dropping keys into `.env` alone would not have fixed it.

Worse, `x-backend-env` passed `GROQ_API_KEY` but **not** `GROQ_MODEL`, so a
`.env` override could never have reached the container. The committed default
was the only thing that mattered.

Fixed, minimally and with no new features:

- `app/core/config.py` — defaults moved to `openai/gpt-oss-120b` (Groq) and
  `gpt-oss-120b` (Cerebras), each verified against the provider's live
  `/v1/models` first.
- `docker-compose.yml` — `GROQ_MODEL`/`CEREBRAS_MODEL` now passed through, so a
  future retirement can be corrected without a rebuild.
- `.env.example` — documents the override and why a stale id is dangerous.

No test referenced either model id, and the AI tests monkeypatch the keys in
both directions, so they stay hermetic. **431 passed** after the change.

**The demo then produced real prose.** `scripts/ai_demo.py`, end to end against
the fresh stack, `provider=groq model=openai/gpt-oss-120b cached=False`:
all checks passed across all three demos, including the propose → human confirm
→ execute chain and its audit trail.

Grounding was checked objectively, not by reading it and being impressed: every
money-scale number in the prose was extracted and matched against the
authoritative facts the context layer assembled. **All grounded.** The model
also correctly kept the frozen `CONTRACT_WAGE` (25000.00) distinct from the
contract's present wage (60000.00) — the two are different numbers in the facts
and it did not conflate them.

**It never touches a Payslip number.** `app/ai` and `app/mcp` contain zero
occurrences of `Payslip(`, `PayslipLine(`, `.compute(`, `resolve_salary_structure`,
`session.add` or `.flush()`; and a live sweep of four AI task types left the
sha256 of all 9 payslips' gross, net and lines **byte-identical**.

### The honest risk that remains: Groq free-tier TPM, and a dead fallback

Running four AI questions at once reproduced this, live:

```
groq     429 Rate limit ... tokens per minute (TPM): Limit 8000, Used 5899
cerebras 402 Payment required to access this resource
→ All AI providers failed → clean ai_unavailable, facts intact
```

Two things follow, and both matter for the demo:

- **The graceful-degradation path is now verified against real providers**, not
  a stub. It behaves exactly as designed.
- **The Cerebras fallback is dead** — the key authenticates but every completion
  is 402, so there is effectively **one** provider, not two. Combined with an
  8000 TPM ceiling, **asking several AI questions in quick succession during the
  demo will hit "unavailable"**. Ask them one at a time, or add credit to either
  provider. This is a live-demo risk, stated plainly rather than left unknown.

### Note

`scripts/ai_demo.py` creates an August payrun and a time-off request in the
`peoplepay360` demo database by design. It was run twice here, so that database
has moved further from pristine — the reset advice above still stands.
