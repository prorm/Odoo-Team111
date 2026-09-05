# PeoplePay360 — System Architecture (v2)

Base repo: fork ForgeERP in full (`git clone` → `peoplepay360/`). This document supersedes v1. The change from v1: ForgeERP's advanced infrastructure (AI, MCP, offline sync, realtime, observability) is **reinstated**, adapted to the HR/Payroll domain, and organized as an explicit **Platform/Intelligence layer** sitting alongside the **Core Domain**, both built on the same Shared Business Layer. AssetFlow's actual domain entities (Asset, TransferRequest, ResourceBooking, MeetingRoom) are still removed — wrong domain regardless of which infra survives.

```
                         PEOPLEPAY360
        ┌─────────────────────┬─────────────────────┐
        │    CORE DOMAIN       │  PLATFORM / INTELLIGENCE │
        │  Employee            │  Auth / RBAC             │
        │  Contract            │  Audit                   │
        │  WorkingSchedule     │  Idempotency              │
        │  Attendance          │  Concurrency (OCC/EXCLUDE)│
        │  TimeOffType/        │  Taskiq                   │
        │   Allocation/Request │  AI (provider routing)    │
        │  SalaryStructure/Rule│  MCP (agent tooling)       │
        │  Payrun / Payslip    │  Offline Sync              │
        │  Dashboard           │  Realtime (WS/SSE)         │
        │                      │  OTel/SigNoz + Sentry      │
        └──────────┬───────────┴────────────┬──────────────┘
                   └──────────────┬──────────┘
                                  ↓
                     SHARED BUSINESS LAYER
        (Repository → Service → Validation → Transaction → Audit → DB)
```

**The one rule that makes this safe:** every entry point — human UI, MCP tool call, offline-sync push, AI-initiated action — terminates in the exact same service-layer call as every other. There is no second implementation of business logic anywhere in the Platform/Intelligence layer. See §11.

---

## 1. Tech Stack

### Core (unchanged from v1)
| Layer | Technology |
|---|---|
| Backend | FastAPI (Python 3.12), SQLAlchemy 2.0 async (`asyncpg`), Alembic, Pydantic v2 |
| Database | PostgreSQL 16 |
| Cache | Redis 7 |
| Background jobs | Taskiq (Redis broker) |
| Auth | JWT (`python-jose`) + `passlib[bcrypt]` |
| Frontend | React 19 + Vite + TS, React Router v7, TanStack Query, Shadcn/ui, Tailwind |
| Payslip PDF | `weasyprint` (HTML/CSS → PDF) via a shared Jinja2 template |
| Bulk email | `aiosmtplib` |
| Salary formulas | `simpleeval` (restricted grammar, never `eval()`) |
| Dashboard charts | `recharts` |

### Reinstated — Platform/Intelligence layer
| Layer | Technology | Notes |
|---|---|---|
| AI inference | Groq (primary) + Cerebras (fallback), via ForgeERP's `AIProvider` router | Retargeted at HR/Payroll explanation/investigation prompts, never at computing money |
| AI orchestration | FastMCP, separate process, Streamable HTTP transport | HR/Payroll tool set (§9) replaces the original asset/booking tool set |
| Offline | `idb` (IndexedDB) on frontend, `SyncMutation` + sync registry on backend | Re-scoped from Notes/Asset to Attendance + TimeOffRequest only |
| Realtime | FastAPI native WebSockets | Presentation layer only, over committed DB state |
| Observability | OpenTelemetry SDK → SigNoz + Sentry | Instruments 3 specific traces (§10), not a generic showcase |

---

## 2. ForgeERP Modules — Retained / Adapted / Removed

### Retained unchanged (domain-agnostic infra)
| Module | Use in PeoplePay360 |
|---|---|
| Repository Pattern + Service Layer base classes | Every entity, core and platform alike |
| Hashids `public_id` | New prefixes: `emp_`, `ctr_`, `wsch_`, `att_`, `tot_`, `alloc_`, `req_`, `sstr_`, `srule_`, `prun_`, `pslip_` |
| JWT auth + `require_role` dependency mechanism | Vocabulary replaced (§6), mechanism unchanged |
| Money as `Numeric`/`Decimal` (Rule 9) | Every wage, rule amount, payslip line |
| Real optimistic concurrency (`version_id_col`) | Contract, Payrun, Payslip, TimeOffAllocation |
| Postgres `EXCLUDE` constraint pattern (Rules 5/8) | Contract non-overlap — same shape as booking-overlap |
| Advisory lock (`acquire_entity_lock`) | Payrun "Compute" rewriting its Payslip set |
| `Idempotency-Key` middleware | Payrun create/compute |
| Audit log (append-only) | Expanded list, §12 |
| Frontend bare tooling (Shadcn, Tailwind, `AppShell`, `fetchApi`) | All HR screens |
| Docker Compose, CI shape | Trimmed service list, not deleted patterns |

### Adapted (kept, retargeted at HR/Payroll — built in Phases 8–10, not Phase 0–7)
| Module | Original target | New target |
|---|---|---|
| `app/ai/provider_router.py`, `cache.py`, `decision_nodes.py` | Generic transfer-approval AI decisions | HR/Payroll Q&A + anomaly interpretation; `decision_nodes.py` becomes the "AI proposes a mutation, human confirms" pattern for AI-initiated writes only |
| `app/mcp/server.py` | Asset/booking tools | HR/Payroll read + controlled-action tools (§9) |
| Offline sync (`sync_registry.py`, `sync_entities.py`, `sync.py`, `SyncMutation`, frontend `offline-db.ts`/`reachability.ts`/`sync-engine.ts`/`useOfflineMutation.ts`/`OfflineBanner`/`ConflictModal`) | `note`, `asset` | `attendance`, `time_off_request` registered as the syncable entities instead — same generic engine, new registrations, zero new sync-engine code |
| `app/realtime/ws_manager.py` (never built in ForgeERP) | n/a | Check-in/leave-request/approval/payroll-progress broadcast |
| OTel/SigNoz | Generic instrumentation | 3 named traces only (§10) |

### Removed (wrong domain, no amount of "keep the infra" argument saves these)
| Removed | Reason |
|---|---|
| `Asset`, `TransferRequest`, `ResourceBooking`, `MeetingRoom` models/services/routers, `resource_registry.py`, `booking.py` | AssetFlow's actual domain entities — no HR equivalent, no reason to keep |
| `Notes` entity | Existed only as the original sync-proof entity; superseded by Attendance/TimeOffRequest as the real sync targets |
| Generic domain workflow/state-machine engine (`app/workflows/*` beyond the AI-decision-node pattern) | Time Off approve/refuse is a 2-state transition; a generic engine costs more than a status field saves. The **AI decision node pattern specifically** is kept (see Adapted table) because it's the mechanism for AI-confirmed mutations, not a domain workflow |
| pgvector / semantic search | No documents/assets domain to search |

---

## 3. Repository Structure

```
forgeerp/backend/app/
├── models/
│   ├── employee.py, contract.py, working_schedule.py, attendance.py   # NEW
│   ├── time_off.py, salary.py, payroll.py                              # NEW
│   ├── department.py, user.py                                          # KEEP/EXTEND
│   ├── asset.py, transfer.py, booking.py, meeting_room.py, note.py     # DELETE
│   └── mixins.py                                                        # KEEP (AuditedEntity, version_id_col)
├── repositories/            # one per entity, extends BaseRepository — unchanged pattern
├── services/
│   ├── employee.py, contract.py, schedule.py, attendance.py            # NEW (Phase 1-2)
│   ├── time_off.py, salary.py, payroll.py, dashboard.py                # NEW (Phase 2-6)
│   ├── ai/                                                              # ADAPTED (Phase 9): provider_router.py, cache.py, decision_nodes.py
│   ├── sync_registry.py, sync_entities.py, sync.py                     # ADAPTED (Phase 8): register attendance/time_off_request
│   ├── booking.py, resource_registry.py                                 # DELETE
├── api/v1/routers/
│   ├── employees.py, contracts.py, schedules.py, attendance.py         # NEW
│   ├── time_off.py, salary.py, payroll.py, dashboard.py                # NEW
│   ├── sync.py                                                          # ADAPTED (Phase 8)
│   ├── assets.py, bookings.py, notes.py                                # DELETE
├── mcp/
│   └── server.py                                                        # ADAPTED (Phase 9): HR/Payroll tool set, §9
├── realtime/
│   └── ws_manager.py                                                    # ADAPTED (Phase 10)
├── jobs/tasks/
│   ├── payroll_jobs.py                                                  # NEW (Phase 5): PDF + email
│   └── ai_jobs.py                                                       # ADAPTED (Phase 9)
└── templates/payslip.html.j2                                            # NEW (Phase 5)
```

Frontend diff mirrors this: new `routes/` per HR module (Phase 1-7), `lib/offline-db.ts` etc. re-scoped to attendance/time-off (Phase 8), `hooks/useRealtimeChannel.ts` new (Phase 10), `NotesPage.tsx`/`ConflictModal` content re-pointed at Attendance/TimeOff instead of deleted outright.

---

## 4. Core Domain Model (unchanged from v1)

```
User (extend: role enum — employee | hr_manager | hr_payroll_user | hr_payroll_manager | admin)
Department (reuse existing model as-is)
Employee (user_id?, department_id, manager_id self-FK, job_position, status, default_schedule_id, bank_account)
Contract (employee_id, department_id, job_position, wage:Numeric, salary_structure_id, start_date, end_date?, status)
  CONSTRAINT: EXCLUDE non-overlapping active date ranges per employee
WorkingSchedule (name, type, weekly_hours:computed) + ScheduleLine(day, start, end, break)
Attendance (employee_id, check_in, check_out, worked_hours:computed, status, corrected_by?)
TimeOffType (name, unit, requires_allocation, requires_approval, payroll_integration)
TimeOffAllocation (employee_id, time_off_type_id, allocated, taken, remaining:derived, valid_from/to, status)
TimeOffRequest (employee_id, time_off_type_id, date_from/to, duration:derived, status, approved_by?)
SalaryStructure (name, is_active) + SalaryStructureRule(rule_id, sequence)
SalaryRule (name, code:unique, category, sequence, computation_method: fixed|percentage|formula, value/expression)
Payrun (name, salary_structure_id, period_start/end, status) + PayrunEmployee(employee_id)
Payslip (payrun_id, employee_id, contract_id, worked_days, status, warnings:JSONB) + PayslipLine(rule_id, code, category, amount:Numeric)
```

All entities except pure line-item children get `AuditedEntity` (audit + `version_id_col`).

---

## 5. RBAC — Exact Permission Matrix

| Module | Employee | HR Manager | HR Payroll User | HR Payroll Manager | Admin |
|---|---|---|---|---|---|
| Own profile/attendance/leave balance | R | — | — | — | — |
| Employees | — | CRUD | CRUD | CRUD | CRUD |
| Contracts | — | CRUD | CRUD | CRUD | CRUD |
| Working Schedules | — | CRUD | CRUD | CRUD | CRUD |
| Attendance (own) | CR | CRUD | CRUD | CRUD | CRUD |
| Time Off Requests (own create) | CR | CRUD + approve/refuse | CRUD + approve/refuse | CRUD + approve/refuse | CRUD + approve/refuse |
| Salary Structures/Rules | — | — | R | CRUD | CRUD |
| Payruns/Payslips | — | — | CRU | CRUD | CRUD |
| User mgmt / role assignment | — | — | — | — | CRUD |

**This matrix applies identically to MCP tool calls and AI-initiated actions** — an MCP tool wraps the same `require_role`-guarded service method the REST router calls; there is no separate, looser permission model for agent access (ForgeERP's original design used a narrower API-key scope for MCP — for PeoplePay360, an MCP session is bound to an authenticated user's role, since the demo scenario is "Claude acting as/for a specific HR user," not an anonymous agent).

---

## 6. Concurrency & Data Integrity (unchanged from v1)

- **Contract overlap**: `EXCLUDE USING gist (employee_id WITH =, daterange(start_date, end_date, '[]') WITH &&) WHERE (status = 'active')`.
- **Optimistic concurrency**: `version_id_col` on Contract, Payrun, Payslip, TimeOffAllocation.
- **Advisory lock**: `acquire_entity_lock(session, "payrun", payrun_id)` wraps Payrun Compute.
- **Idempotency-Key**: required on Payrun create + compute.
- **Money**: `Numeric(12,2)` end to end, `Decimal` in every layer, `str()` across any JSON boundary (Taskiq payload, MCP tool response, AI prompt context).

This holds regardless of entry point: an MCP `create_payrun` tool call and offline-synced attendance both flow through the identical constrained/audited service methods — see §11.

---

## 7. Payroll Computation Engine (unchanged from v1 — deterministic, no AI involvement)

1. Resolve the one applicable contract for `(employee, payrun.period)` — uniqueness guaranteed by §6's constraint.
2. Load the contract's `SalaryStructure` → ordered rules.
3. Build a computation context (`WORKED_DAYS`, `CONTRACT_WAGE`, `UNPAID_LEAVE_DAYS`, etc.) from Attendance + approved Time Off.
4. Execute each `SalaryRule` in sequence (fixed / percentage / `simpleeval` formula), storing each result under its `code` so later rules can reference it.
5. Categorize into Basic/Allowances/Gross/Deductions/Net.
6. Run warning checks (missing bank details, duplicate payslip, contract gap) → `Payslip.warnings`.

**AI is architecturally incapable of writing to `Payslip`/`PayslipLine`.** The AI layer and MCP tools can *read* payslip data and *narrate* it (§8.1, §8.6); no AI code path calls the rule engine's write methods.

---

## 8. Platform / Intelligence Layer — Detailed Design

### 8.1 AI Layer
`app/ai/provider_router.py` (Groq → Cerebras fallback, unchanged mechanism from ForgeERP) is retargeted with HR/Payroll prompt templates for: payslip explanation, department payroll variance explanation, "who is blocking payroll," anomaly narration, pending-actions summarization. Every AI call is queued through Taskiq (unchanged from ForgeERP — routers enqueue, return `202`, frontend polls/listens), Redis-cached by `(prompt, context, task_type)`, and rate-limited per provider.

`app/ai/decision_nodes.py` is repurposed specifically for **AI-initiated mutations**: an AI decision node proposes an action (e.g., "submit 1 day of leave for Rahul next Friday") with a written rationale, sets state to `PENDING_REVIEW`, and a human must explicitly confirm before the identical authorized service method actually runs. The proposal, the AI's raw output, and the human confirmation are all written to the audit log.

### 8.2 MCP Layer
`app/mcp/server.py`, FastMCP, Streamable HTTP, separate process (unchanged deployment shape). Tool set, hand-written per ForgeERP's own rule (no `FastMCP.from_fastapi()` auto-conversion):

**Read tools:** `get_employee`, `get_employee_contracts`, `get_attendance_summary`, `get_leave_balance`, `get_pending_time_off`, `get_payrun_summary`, `get_payslip`, `explain_payslip`, `get_payroll_warnings`, `get_department_payroll`, `get_payroll_trends`, `find_payroll_anomalies`, `find_contract_conflicts`.

**Controlled action tools:** `create_time_off_request`, `approve_time_off_request`, `correct_attendance`, `create_payrun`, `request_payroll_validation`.

Every mutating tool: authenticates → authorizes via §5's matrix → invokes the same service the UI calls → runs full validation → writes an audit event with `actor = "ai-agent"` (or the impersonated user, per §5) → respects idempotency/concurrency identically to the REST path. No tool executes raw SQL or bypasses a repository/service method.

### 8.3 Offline Sync Layer
ForgeERP's generic sync engine (cursor-based pull, per-op idempotent push, savepoint-per-operation, `Keep Mine`/`Overwrite` conflict resolution) is unchanged mechanically. What changes is the registration: `app/services/sync_entities.py` registers `attendance` (check-in/check-out only, not corrections — corrections stay online-only and role-gated) and `time_off_request` (create only) as the syncable entities, replacing `note`/`asset`. Payroll, Salary Rule, Contract, and every other core entity is deliberately **not** registered — offline capability is opt-in per entity by registry membership, and payroll entities are never opted in.

```
DEVICE → IndexedDB outbox → [offline] → queued mutations → [reconnect]
  → sync engine push → backend validation (same service layer) → transaction
  → audit → pull → UI reconciliation
```

Client mutation IDs + the existing `Idempotency-Key`/`sync_mutations` idempotency check prevent duplicate attendance/leave records across retries — this is the same mechanism already proven in ForgeERP's offline-sync pass, just pointed at new entities.

### 8.4 Realtime Layer
Native FastAPI WebSockets, one channel per concern: attendance check-ins (broadcast to HR dashboard), new time-off requests (broadcast to approvers), approval outcomes (broadcast to the requesting employee), payroll compute/bulk-email progress (broadcast to the initiating Payroll user's session). Every broadcast fires **after** a committed transaction — the socket is a notification of already-true state, never a store of truth. If the socket layer is down, REST/refetch still produces correct results; this is why realtime is P1/P2, not P0.

### 8.5 Observability
Three named traces only:
- **Payroll compute**: `POST /payruns/{id}/compute` → resolve contract → load schedule → load attendance → load leave → load structure → execute rules → write payslip → validate → queue jobs.
- **AI/MCP**: Claude → MCP tool → service → DB → AI response.
- **Offline sync**: client mutation → sync attempt → validation → DB transaction → audit.

Sentry remains for error tracking as already proven in ForgeERP. No instrumentation is added outside these three traces — the goal is a demonstrable "every payroll calculation is traceable" moment, not blanket tracing.

### 8.6 Explainability, Anomaly Detection, Time Machine, Pay-Change Comparison, Validation Firewall, Simulation
These are UI/service features built on top of core data, described fully in the PRD (§5.6–§5.11). Architecturally they are all **read-side** features (except Simulation, which is explicitly non-persistent) — none of them write to Payslip/Contract/Payrun outside the normal core flows:
- **Explainability**: a view assembling `PayslipLine` rows into a tree with each line's rule/sequence/inputs — pure read/render.
- **Anomaly detection**: deterministic queries over existing tables producing warnings; optional AI narration layered on top (§8.1).
- **Time Machine**: a read view over `Contract` date ranges plotted against a selected payroll period.
- **Pay-change comparison**: a read view diffing two `Payslip`/`PayslipLine` sets for the same employee.
- **Validation Firewall**: a read-side aggregation of `Payslip.warnings` across a Payrun, plus navigation links — no new write path.
- **Simulation**: runs the §7 engine in a non-persisting mode (compute in-memory, never call the repository's write methods), clearly labeled in the UI.

---

## 9. Security Principle — One Path to the Database

```
Human UI  →  API  →  Service  →  Validation  →  Transaction  →  Audit  →  DB
Claude    →  MCP  →  (same) API/Service call  →  (same) →  (same) →  DB
Offline   →  Sync API  →  (same) Service call  →  (same) →  (same) →  DB
```

No alternate path may bypass RBAC, business rules, idempotency, or audit. Concretely: the MCP server and the sync-push endpoint both call into `app/services/*` — they do not have their own copies of contract-overlap logic, allocation-deduction logic, or payroll-computation logic. This is enforced by code review discipline (Phase 9/10 prompts explicitly instruct the agent to import and call existing services, never reimplement) rather than a technical barrier alone.

---

## 10. Money / Payroll Safety (unchanged, restated because it's the one rule that cannot slip)

`Numeric(12,2)`/`Decimal` everywhere monetary. No `Float`, ever, in the payroll path. AI may read and narrate payroll numbers; it has no code path capable of writing them. The rule engine (§7) is the sole authority for every figure that appears on a Payslip.

---

## 11. Auditability

Audited: contract changes, attendance corrections, leave approvals/refusals, salary-rule/structure changes, payrun transitions, payslip generation, payroll validation, **AI-assisted actions** (proposal + rationale + human confirmation), **MCP mutations** (actor = ai-agent or impersonated user), **offline sync mutations** (client mutation ID, resolved conflict if any). A human action and an AI-confirmed action leave equivalent, equally inspectable audit rows — there is no "less audited" path.

---

## 12. Deployment

`docker-compose.yml` restores the MCP server and AI-provider-dependent worker as **optional Compose profiles** (`--profile advanced`) so the core team can run `docker compose up postgres redis backend worker frontend` without needing Groq/Cerebras keys or the MCP process during Phases 1–7, and bring up the full stack (`--profile advanced` adds `mcp-server`, `signoz`) starting Phase 8. This directly supports the "core first, advanced later" build order without requiring two separate compose files.
