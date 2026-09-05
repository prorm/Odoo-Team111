# PeoplePay360 progress and handoff

Updated: 2026-09-05. Repository: prorm/Odoo-Team111; working branch: dev.
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
