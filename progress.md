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

## Delivery

Implementation, tests, and this handoff are committed together as the Phase 2
change. See git log for the commit hash and origin/dev for the pushed version.
No platform/intelligence work or palette changes are included.

## Next work

Continue core salary structure/rule authoring and deterministic payroll
(Phases 3–5), followed by reporting/dashboard and core polish (6–7).
Re-read the original requirements for the next phase before implementation.
Never use LLMs for payroll arithmetic; use Decimal and the salary rule engine.
Reuse the service-level permission and transaction paths established here.
Do not register HR sync entities or add AI/MCP integrations until core is ready.

Potential follow-ups must preserve current policies explicitly:
organization timezone/calendar configuration, finer-grained hour requests,
approved-request cancellation with credit, day-by-day absence reporting, and
admin user/employee linking UI. These are not silently implemented assumptions.
