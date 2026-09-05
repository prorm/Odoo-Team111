# PeoplePay360 — Product Requirements Document (v2)

**Source of truth for scope:** Odoo Hackathon Problem Statement (`PeoplePay360_HR___Payroll.pdf`). Every requirement in it is a hard requirement — nothing in PS §4 (A1–A7, B1–B9) is optional, and nothing below waters that down.
**Team size:** 2 engineers.
**Base codebase:** Harmonix360 (generic FastAPI/React framework, proven infra for repository/service patterns, audit, RBAC, AI provider routing, MCP tooling, offline sync, observability — no HR domain content exists in it today).

**v2 change from v1:** v1 stripped Harmonix360's AI/MCP/offline-sync/realtime/observability infrastructure because the PS doesn't ask for it. That decision is reversed. The PS remains the entire mandatory core — untouched — and Harmonix360's advanced infrastructure is reinstated as an explicit, optional **differentiation layer** built on top of it, after the core is demoable. See §5.

---

## 1. Product Philosophy

PeoplePay360 is two layers on one shared foundation:

```
                         PEOPLEPAY360
                              │
        ┌─────────────────────┴─────────────────────┐
        │                                            │
   REQUIRED CORE (PS)                    DIFFERENTIATION (optional)
        │                                            │
 Employee / Contract                          AI Intelligence
 Schedule / Attendance                        MCP / Agent Access
 Time Off                                     Offline Sync
 Salary Rules                                 Realtime Updates
 Payrun / Payslip                             Explainability
 Dashboard                                    Anomaly Detection
 PDF / Email                                  Observability
 RBAC                                         Simulation / Automation
        │                                            │
        └─────────────────────┬─────────────────────┘
                               ↓
                     SHARED BUSINESS LAYER
          (Services · Validation · Transactions · Audit · DB)
```

**Rule that governs every decision in this document:** the differentiation layer is additive. It is built on top of the same services, validation, RBAC, and audit trail as the core — never a parallel path, never a shortcut, and never allowed to replace, weaken, mock, or defer a core requirement. If a differentiation feature and a core requirement ever compete for time, the core wins, unconditionally.

**Build order:** the core (§4) is built and demoable end-to-end first. The differentiation layer (§5) is built afterward, on top of a working core — never in parallel with an unfinished core. This is reflected directly in the phase plan (`03_DEVELOPMENT_PLAN.md`): Phases 1–7 are core-only; Phases 8–10 add offline, AI/MCP, and realtime/observability; Phase 11 is demo hardening.

## 2. Problem Statement (condensed)

HR tools that store employees, attendance, leave, and salary as separate, disconnected records fail real HR/payroll teams. An employee can have multiple contracts over time, but payroll must resolve the one contract valid for the period being run. Working hours come from an assigned schedule; attendance has exceptions needing review; leave balances depend on allocations and approvals; payroll must transform all of it into a validated, auditable payslip.

## 3. Target Users (exactly as defined in the PS)

| Role | Access |
|---|---|
| **Employee** | View own profile, attendance, leave balances. Create attendance entries and Time Off Requests. No HR/payroll admin. |
| **HR Manager** | Full CRUD: Employees, Attendance, Contracts, Working Schedules, Time Off. Approve/refuse Time Off. No payroll access. |
| **HR Payroll User** | All HR Manager rights + Create/Read/Update on Payruns & Payslips. Read-only Salary Structures/Rules. |
| **HR Payroll Manager** | All HR Payroll User rights + full CRUD on Payruns, Payslips, Salary Structures, Salary Rules. |
| **Admin** | Full access to everything, incl. user management, role assignment, system administration. |

This matrix is enforced server-side for every access path — human UI, MCP/AI tool calls, and offline sync alike (see §5's Security Principle).

## 4. Core Scope — Mandatory, Unchanged From the PS

Directly from PS §4 — nothing added, nothing dropped, nothing weakened by the differentiation layer:

### A) HR Backend / Configuration
- **A1 Employee Master** — Kanban + List + Form; department, manager, schedule, job position, status; quick links to related Contracts/Attendance/Time Off.
- **A2 Contract Management** — historical records per employee; list highlights the active contract; **payroll resolves exactly one contract per period — no concurrent active contracts, enforced at the database level.**
- **A3 Working Schedule** — weekly pattern (Day/Start/End/Break); **weekly hours auto-computed, never manually entered.**
- **A4 Time Off Type & Allocation** — Types define units/allocation-requirement/approval/payroll-integration; Allocations track taken/remaining/validity; approved requests auto-deduct from allocations.
- **A5 Salary Structure** — ordered container of Salary Rules; a Payrun's structure dictates which rules run.
- **A6 Salary Rule** — Name/Code/Category/Sequence; fixed/percentage/formula computation, executed in sequence.
- **A7 Reporting Config** — dashboard aggregates live HR+Payroll data, filterable by Period/Department/Employee Type.

### B) HR & Payroll Frontend
- **B1** Top nav: Employees, Contracts, Attendance, Time Off, Payroll, Reports.
- **B2** Employee Form as hub, with smart-button navigation to filtered related records.
- **B3** Attendance: Check In/Out/Worked Hours/Status; corrections restricted to authorized roles.
- **B4** Time Off Requests: approve/refuse workflow; approved requests reduce balances automatically.
- **B5** Payrun wizard: Step 1 (Structure + Period) → Step 2 (explicit employee selection) → Create Payrun.
- **B6** Payrun processing: Compute / Validate / Mark Paid / Send Payslips; warnings surfaced pre-finalization; finalized runs preserved as history.
- **B7** Payslip: rule-by-rule breakdown (Basic/Allowances/Deductions/Gross/Net), using the period-applicable contract.

  Loss of Pay is a deduction rule consuming `LOP_AMOUNT = (CONTRACT_WAGE / SCHEDULE_WORKING_DAYS_IN_PERIOD) * UNPAID_LEAVE_DAYS`, with Decimal arithmetic and final two-place `ROUND_HALF_UP`. Scheduled working days are inclusive period dates with positive net hours from the existing attendance schedule-expectation logic (contract override, otherwise employee default). A missing/deleted schedule or zero working days raises a BLOCKING `lop_schedule_unavailable` finding; no fallback denominator or invented amount is allowed, including when unpaid leave is zero. Fix the schedule and recompute before Validate.

  `missing_checkout` is BLOCKING at the Validate firewall. Correcting attendance alone does not clear its stored finding: recompute is required. Paid payslips retain their stored lines, totals, input context, and employee/contract/period reference snapshot across later contract or employee edits; detail, calculation display, and future PDF reads must use that history without live repricing. Legacy payslips without reference snapshots fail clearly rather than silently substituting today's contract: recompute only unfinalized runs; finalized history requires historical evidence.

- **B8** Payslip PDF generation + bulk email.
- **B9** Payroll Dashboard: KPIs, charts (Salary Cost by Department, Monthly Net Salary Trend), operational alerts, attendance/leave overview, department breakdown — all query-backed, never static.

### Core User Stories

**Employee** — view own profile/attendance/leave balances; submit and track own Time Off Requests; log own attendance.
**HR Manager** — manage Employees/Contracts/Schedules/Attendance/Time Off; approve/refuse leave; cannot touch payroll.
**HR Payroll User** — create/update Payruns/Payslips; read-only on Salary config.
**HR Payroll Manager** — everything above + full Salary Structure/Rule authoring + run payroll end-to-end.
**Admin** — full access, user/role management.

## 5. Differentiation & Advanced Capabilities (Not required for baseline acceptance, but intentionally supported as advanced product capabilities)

These are **not** core acceptance criteria. They do not gate the hackathon deliverable. They are explicitly designed, and built after the core is stable, to make the product a technically ambitious, AI-native, resilient, and observable platform — not a bare CRUD app.

### 5.1 AI Intelligence
Reinstated from Harmonix360's provider-routing infrastructure (Groq → Cerebras fallback), adapted to HR/Payroll. **AI never calculates payroll** — deterministic Salary Rules remain the sole authority for money. AI's role is explanation, investigation, and controlled, human-confirmed action:
- "Why did Rahul's salary increase this month?"
- "Why is Engineering payroll 14% higher?"
- "Which employees are blocking payroll?"
- "Find unusual salary changes." / "Show attendance anomalies." / "Which contracts expire soon?"
- "Summarize pending HR actions."

For any mutating request, AI proposes → explains → asks confirmation → calls the same authorized service every human action calls → gets audited, exactly like a human action, and can never bypass RBAC.

### 5.2 MCP / Agent Tooling
Reinstated FastMCP server exposing safe, curated business capabilities — never raw SQL, never unrestricted DB access. Read tools (`get_employee`, `get_leave_balance`, `get_payrun_summary`, `explain_payslip`, `find_payroll_anomalies`, `find_contract_conflicts`, etc.) and controlled action tools (`create_time_off_request`, `approve_time_off_request`, `correct_attendance`, `create_payrun`) — every mutating tool authenticates, authorizes, runs full validation, and audits, through the identical service layer the UI uses.

### 5.3 Offline Sync
Reinstated Harmonix360's offline layer (IndexedDB outbox, reachability detection, sync engine, idempotency, optimistic concurrency, conflict handling), re-scoped from its original proof entities (Notes/Asset) to **Attendance check-in/check-out and Time Off Request creation only** — the two operations an employee plausibly needs offline. Payroll mutations are never offline-capable. No duplicate records survive a retried sync (client mutation IDs + Idempotency-Key + versioning).

### 5.4 Realtime
Optional WebSocket/SSE presentation layer over committed DB state — REST/API remains authoritative. Used for: a check-in appearing instantly on the HR dashboard, a new leave request appearing instantly for the manager, a balance updating instantly for the employee on approval, live payroll-compute and bulk-email-send progress.

### 5.5 Observability
Reinstated OpenTelemetry → SigNoz + Sentry, instrumenting three specific business traces (not a generic showcase): the payroll-compute trace (contract resolution → schedule → attendance → leave → rules → payslip → jobs), the AI/MCP trace (Claude → MCP tool → service → DB → response), and the offline-sync trace (client mutation → validation → transaction → audit).

### 5.6 Payslip Explainability
A "View Calculation" action on every Payslip renders a deterministic calculation tree — every amount traceable to its Salary Rule, sequence position, and inputs (contract, attendance, leave). AI may narrate this tree afterward; it never generates the numbers.

### 5.7 Payroll Anomaly Detection
Deterministic rule checks (large salary jump, unusual overtime, missing bank details, missing checkout, overlapping contract, low attendance, contract expiring, duplicate payslip attempt, department spend spike) surface as warnings; AI may add optional interpretation on top. An LLM never decides whether a payroll transaction is valid.

### 5.8 Contract "Time Machine"
Visual timeline making period-specific contract resolution obvious — selecting a payroll period highlights the contract that period actually resolves to, and historical payroll stays tied to the contract that was applicable when it ran.

### 5.9 "Why Did Pay Change?" Comparison
Period-over-period payslip diff (e.g., August → September) grounded entirely in real payslip records, with an optional AI summary of the diff.

### 5.10 Payroll Validation Firewall
A highly visible pre-finalization gate on the Payrun screen: blocking issue count, categorized (missing bank details, overlapping contract, missing checkouts), with navigation straight to the offending records and a "Revalidate" action once resolved.

### 5.11 Payroll Simulation (lowest priority, build only if time remains)
Non-destructive what-if mode (e.g., "what if HRA were 60% instead of 50%?") showing projected Gross/Net deltas without touching official records. Clearly labeled SIMULATION at all times.

## 6. Priority Tiers

| Tier | Contents |
|---|---|
| **P0 — must exist for the hackathon to be valid at all** | Employee, Contract, Schedule, Attendance, Time Off, Salary Rules, Payrun, Payslip, Dashboard, RBAC, PDF, Email — all of PS §4, all PS business rules |
| **P1 — strong differentiators, build if P0 is solid** | Validation Firewall, Payslip Explainability, Contract Time Machine, better realtime UX on core screens, offline attendance/leave |
| **P2** | AI explanations, MCP tools, anomaly intelligence, AI-initiated actions (with confirmation) |
| **P3** | Payroll simulation, additional polish |

If time runs out, P3 drops first, then P2, then P1 — **P0 never drops.** Nothing in P1–P3 may be built by cutting corners in P0.

## 7. Success Metrics

### Core (hackathon acceptance — unchanged from v1)
- Two end-to-end scenarios run live without manual DB edits: employee→contract→schedule→payrun→payslip→PDF→email, and time-off-allocation→request→approval→balance-update.
- Zero hardcoded/mocked dashboard numbers.
- A duplicate-payslip attempt is caught and surfaced as a warning, not silently duplicated.
- An overlapping active contract is rejected at the database level.
- Role-based access enforced server-side for all 5 roles.

### Advanced (differentiation layer — evaluated separately, never substituted for the above)
- AI can explain a real payslip from actual data (no fabricated numbers).
- MCP can safely query live PeoplePay360 records and a mutation tool respects RBAC + audit, verified by attempting the same mutation with an unauthorized role and confirming rejection.
- Offline attendance/leave sync produces zero duplicate records across a kill-network → mutate → reconnect cycle.
- The anomaly engine surfaces at least one real, non-fabricated warning against seeded data.
- A live payroll-compute trace is visible end-to-end in the observability tool.

## 8. Assumptions

- Single-tenant, single organization — no multi-tenancy, no RLS.
- Single currency, no FX.
- Email delivery via a real SMTP relay or a local dev catcher (e.g., Mailhog) for the demo.
- "Bank details" is a simple field (IBAN/account number string) for the missing-bank-details warning — not full verification.
- Attendance check-in/out is manually entered/corrected — no biometric/hardware integration.
- Formula salary rules use a small, safe expression language (`simpleeval`) over named inputs — not a general scripting sandbox.
- Offline capability is deliberately scoped to Attendance + Time Off Request only — not a general offline-everything system, and never extended to payroll mutations.
- AI providers (Groq/Cerebras) are available with API keys for the demo; without them, AI-gated features degrade to "unavailable," never to a fabricated answer.

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Payroll computation engine is the highest-complexity, easiest-to-silently-break piece | Dedicated phase, `Decimal` everywhere, ordered rule execution, hand-verified test payslips |
| Two overlapping active contracts corrupt payroll | Postgres `EXCLUDE` constraint, not just app validation |
| Duplicate payslips from a double-click/retry | `Idempotency-Key` middleware on Payrun create/compute |
| Concurrent edits lose an update | `version_id_col` optimistic concurrency |
| **Differentiation layer eats time needed for the core** | Hard sequencing: Phases 1–7 (core) complete and demoable before Phase 8+ (advanced) starts — see `03_DEVELOPMENT_PLAN.md` |
| **AI/MCP/offline introduce an authorization bypass** | Single architectural rule: every path (UI, MCP, offline sync) terminates in the same service layer, same RBAC check, same audit write — no alternate path to the DB (see Architecture §11) |
| **AI silently produces a wrong payroll number** | AI is explanation/investigation-only; it is architecturally incapable of writing to Payslip/PayslipLine — only the deterministic rule engine can |
| Dashboard becomes hardcoded numbers under time pressure | Every KPI is a query-backed acceptance criterion |

## 10. Not Restored / Explicitly Out of Scope

A few Harmonix360 capabilities remain cut because there's no analogous PeoplePay360 use case, not because "PS-only" thinking crept back in:

- **pgvector semantic search** — Harmonix360's version was "semantic search over assets/documents"; there's no assets/documents domain here, and nothing in §5's differentiation list calls for document search. Not restored.
- **Generic AssetFlow domain entities** (`Asset`, `TransferRequest`, `ResourceBooking`, `MeetingRoom`) — wrong domain regardless of which infra layer is kept.
- **Generic state-machine workflow engine** — Time Off approve/refuse stays a simple status transition. The reinstated "AI decision node" pattern (propose → human confirm → execute) is used specifically for AI-initiated mutations (§5.1), not as a general-purpose workflow engine for ordinary HR actions.
- **Multi-tenancy / Row-Level Security** — no requirement, no reason to add.
- **Multi-currency / multi-country statutory compliance / tax-authority integration** — future roadmap, not this build.

## 11. Acceptance Criteria (unchanged from v1 — this is the hard gate)

- [ ] **Functional platform**: fully operational, populated with representative data (seed script).
- [ ] **Live demo, two end-to-end scenarios**: employee→payslip flow, and leave-allocation→request flow — both live, no static data.
- [ ] **Future roadmap** note delivered alongside the demo.
- [ ] All 9 B-features (B1–B9) present and wired to real backend data.
- [ ] All 7 A-features (A1–A7) present with functioning CRUD and real business rules.
- [ ] RBAC matrix enforced server-side for all 5 roles.
- [ ] Payslip PDF generation + bulk email working end-to-end.
- [ ] *(Advanced, evaluated separately, does not gate the above)* — at least one working demo moment from §5 (e.g., Payroll Validation Firewall or Payslip Explainability) if time permitted past Phase 7.
