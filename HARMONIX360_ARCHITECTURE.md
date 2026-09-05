# Harmonix360 — Final Architecture Roadmap
**Status: LOCKED. This document is the single source of truth for all agents (Claude Code, Codex, Antigravity, etc.) working on this codebase. Do not deviate from stack, folder structure, or naming without updating this file first.**

Target: Harmonix360 Core Platform Architecture and Foundations.

---

## 0. Non-Negotiable Stack

| Layer | Technology | Notes |
|---|---|---|
| Backend | **FastAPI (Python 3.12)** | NOT Express. NOT Node for any backend service. This is the only backend runtime in the system. |
| ORM | SQLAlchemy 2.0 (async, `asyncpg` driver) | No raw SQL except migrations and the perf-critical hashid lookups |
| Migrations | Alembic | Every schema change goes through a migration file. No manual `ALTER TABLE`. |
| Validation | Pydantic v2 | All request/response models are Pydantic. No dict-passing between layers. |
| Database | PostgreSQL 16 (Docker) | Already running — keep container, extend schema |
| Cache | Redis 7 (Docker) | Session cache, AI response cache, rate limiter state |
| Background jobs / queue | **Taskiq** (async-native, Redis broker) | Python-native equivalent of BullMQ. Async-first and type-safe, which fits FastAPI's async request lifecycle directly — a task and its result type are defined together, so a router awaiting a job result gets proper type inference. NOT Celery (sync-first, heavier ops surface — broker + result backend + beat scheduler is overkill for this timeline). NOT Arq (still fine, but Taskiq has a cleaner FastAPI dependency-injection story and native middleware for things like our audit-log-on-every-job requirement). NOT BullMQ (that's Node). |
| Realtime | FastAPI native WebSockets (`fastapi.WebSocket`) | NOT Socket.IO. Native ASGI WebSockets are sufficient and remove a dependency. |
| Auth | JWT via `python-jose` + `passlib[bcrypt]` | Access token + refresh token pair. Stateless. |
| Frontend | **React 19 + Vite + TypeScript** + React Router (v7, library mode) + TanStack Query + Shadcn/ui + Tailwind | NOT Next.js. NOT TanStack Router. See rationale below. |
| AI inference | Groq (primary) + Cerebras (fallback) → local Ollama (last resort) via custom `AIProvider` router | See Section 5 |
| AI orchestration | **FastMCP** — mounted as a second ASGI transport alongside the FastAPI app, reusing the same service layer | See Section 6. NOT the low-level `mcp` SDK or the `fastapi_mcp` bridge package (unmaintained since mid-2025) — FastMCP is the actively maintained, decorator-based standard and the one to build against. |
| Observability | OpenTelemetry SDK → SigNoz (metrics/traces/logs) + Sentry (errors) | See Section 7 |
| Containerization | Docker Compose (single file, all services) | See Section 8 |
| Deployment target | Railway or GCP Cloud Run (free tier) | Decide by Week 3, not before |

**Why React + Vite instead of Next.js:** Harmonix360 is an authenticated, internal dashboard — every screen sits behind a login. Next.js's headline strengths (SSR/SSG, file-based server routing, SEO optimization) solve a public-content problem this app doesn't have, and they add real cost: server-vs-client-component boundaries, hydration edge cases, App Router caching quirks — extra surface area and unnecessary complexity. Vite's dev server and HMR are effectively instant, which matters more than anything else during a 24-hour build. The backend is already FastAPI, so Next.js's API-routes/BFF pattern is also redundant — FastAPI *is* the BFF.

**Why React Router instead of TanStack Router:** TanStack Router is objectively more type-safe, but that gain isn't worth its cost for a 24-hour team event. Plain `<Route />`, `useNavigate()`, and `useParams()` are enough for this app's routing needs, every developer knows them, and there is no separate routing paradigm to learn or debug. With React, Query, Tailwind, Shadcn, and FastAPI already in the stack, adding TanStack Router on top is one more advanced library for marginal productivity gain — skip it. Use React Router v7 in library mode (not framework mode — that reintroduces SSR machinery we just removed by dropping Next.js).

**Rule for all agents:** if any instruction elsewhere (old notes, chat history, prior scaffolding) conflicts with this table, this table wins. If Express, Next.js, BullMQ, Socket.IO, Celery, or `fastapi_mcp` code exists anywhere in the repo, it must be deleted and rewritten against the stack above — no hybrid Node+Python backend, no framework mixing.

---

## 1. Repository Structure

```
harmonix360/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI app entrypoint, mounts routers
│   │   ├── core/
│   │   │   ├── config.py            # Pydantic Settings (env vars)
│   │   │   ├── security.py          # JWT, password hashing
│   │   │   ├── database.py          # async engine, session factory
│   │   │   ├── redis.py             # redis client factory
│   │   │   └── telemetry.py         # OpenTelemetry setup, called once at startup
│   │   ├── models/                  # SQLAlchemy ORM models (one file per domain entity)
│   │   ├── schemas/                 # Pydantic request/response models
│   │   ├── repositories/            # Repository Pattern — all DB queries live here, nowhere else
│   │   ├── services/                # Service Layer — business logic, calls repositories
│   │   ├── workflows/               # Workflow engine: state machines, transitions, AI decision nodes
│   │   ├── api/
│   │   │   └── v1/
│   │   │       ├── routers/         # One router file per resource (assets.py, bookings.py, etc.)
│   │   │       └── deps.py          # shared FastAPI dependencies (get_db, get_current_user)
│   │   ├── jobs/
│   │   │   ├── broker.py            # Taskiq broker + Redis backend setup
│   │   │   └── tasks/               # one file per task domain (notifications.py, ai_jobs.py, etc.)
│   │   ├── ai/
│   │   │   ├── provider_router.py   # Groq/Cerebras/Ollama fallback logic
│   │   │   ├── cache.py             # Redis-backed AI response cache
│   │   │   └── decision_nodes.py    # AI-in-workflow logic with audit logging
│   │   ├── mcp/
│   │   │   └── server.py            # FastMCP server exposing domain actions as tools
│   │   ├── realtime/
│   │   │   └── ws_manager.py        # WebSocket connection manager, broadcast helpers
│   │   └── audit/
│   │       └── logger.py            # Immutable audit log writer
│   ├── alembic/                     # migrations
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── main.tsx                 # Vite entrypoint
│   │   ├── router.tsx                # React Router route tree (createBrowserRouter)
│   │   ├── routes/                  # route components, one per resource/screen
│   │   ├── components/              # Shadcn/ui-based shared components
│   │   ├── features/                # one folder per domain (assets, bookings, allocations, ...)
│   │   ├── lib/
│   │   │   ├── api-client.ts        # typed fetch wrapper for the FastAPI backend
│   │   │   └── query-client.ts      # TanStack Query client config
│   │   └── hooks/
│   ├── index.html
│   ├── vite.config.ts
│   └── Dockerfile
├── observability/
│   ├── otel-collector-config.yaml
│   └── docker-compose.signoz.yml
├── docker-compose.yml               # full local stack
├── .env.example
└── HARMONIX360_ARCHITECTURE.md         # this file
```

**Rule:** no business logic in router files. Routers only: parse request → call service → return response. All DB access goes through `repositories/`. This is the Repository Pattern requirement — enforce it in every PR/agent session.

---

## 2. Database Design Rules

1. **Primary keys are `BIGINT` / `SERIAL` internally.** Never expose them to the client.
2. **Every table gets a `public_id` column**: a Hashids-encoded (or UUID-based, see below) string derived from the integer PK, generated at insert time or computed on read. Expose `public_id` in all API responses, accept it in all API inputs. Internal joins and foreign keys always use the integer PK.
   - Use the `hashids` Python library, salted with a per-deployment secret (`HASHID_SALT` env var).
   - This is a deliberate, explainable trade-off: integer PKs keep indexes small and joins fast; hashids prevent ID enumeration and sequential-guessing attacks on the public API.
3. **No Row-Level Security.** Harmonix360 is not multi-tenant — do not implement RLS policies anywhere in the schema, and do not add an `organization_id` tenancy column purely to support it. This removes a real chunk of complexity (policy authoring, per-role grants, testing cross-tenant isolation) that has no payoff for a single-tenant app. Access control is role-based (see Section 4) and enforced at the application/service layer only. If a future version of the product needs multi-tenancy, RLS can be reintroduced deliberately at that point — do not pre-build it "just in case."
4. **Audit log table is append-only.** No `UPDATE` or `DELETE` permission granted to the application's Postgres role on the `audit_log` table — enforce with a `REVOKE` in a migration.
5. **Concurrency-critical resources** (bookings, allocations) use Postgres `EXCLUDE` constraints (range types) to prevent double-booking at the database level — do not rely on application-level locking alone.
6. **pgvector extension** enabled for one AI-driven feature: semantic search over assets/documents (see Section 6).
7. Schema-first: every new domain entity starts with a migration + ERD sketch before any endpoint code is written.
8. **Every `EXCLUDE` constraint carries the predicate that matches its lifecycle.** An unpredicated `EXCLUDE` reserves the range for *every* row, including cancelled and soft-deleted ones — so a cancelled booking keeps blocking its slot, and a soft-deleted booking blocks a slot that no reachable row can explain or release. Any table with soft deletes or a cancellable status must spell that out: `WHERE (deleted_at IS NULL AND status <> 'CANCELLED')`. The matching application-side obligation is a contract on `BaseService` (see its docstring): every mutation method that can move a row *into* the constrained set must translate SQLSTATE `23P01`, not just the obvious INSERT path — a status-only UPDATE is exactly the one that gets forgotten. Reference implementation: `app/services/booking.py`.
9. **Money is `Numeric`/`Decimal`, never `Float`.** This binds Harmonix360 and every fork, on the first migration of any new domain. `double precision` cannot represent 0.10, so any column holding a price, cost, amount, fee, fare, budget, or expense accumulates silent error the moment it is summed or compared against a threshold. Use `Numeric(12, 2)` unless the domain genuinely needs another scale (FX rates, unit prices below a cent), and keep it `Decimal` end to end — models, Pydantic request *and* response schemas, and every calculation. Where money must cross a JSON boundary (a Taskiq job payload, an AI prompt context), serialize it with `str()`; `float()` there throws away the exactness the column exists to provide.
10. **No decorative concurrency columns.** A bare `version` column that nothing reads or increments is worse than no column: it reads as "lost updates are handled here" when they are not. One existed on all 11 audited tables until migration `011_drop_decorative_version` removed it. *TODO (deliberately not built in the 2026-08-10 pass): if Harmonix360 ever needs real optimistic locking, add it as SQLAlchemy's `__mapper_args__["version_id_col"]`, which the ORM actually enforces on UPDATE, and prove it with a concurrent-write test — not as a column that merely exists.*

---

## 2a. Cross-Row Concurrency — Advisory Locks

**File:** `app/core/locks.py`

Constraints (rule 5, rule 8) protect a **row**. They cannot protect an invariant that spans **several rows of the same parent** — "these child rows are numbered 1..n with no gaps", "these line items sum to the parent's total". An operation that rewrites a whole parent-scoped collection reads the set, computes a new arrangement, and writes it back; two of those running concurrently on the same parent interleave their read and write phases and produce a set neither intended, with every individual row still passing every constraint.

The standard pattern for that case is `await acquire_entity_lock(session, entity_type, entity_id)`, wrapping `pg_advisory_xact_lock(hashtext(:key))` with a bound parameter. Call it at the **start** of the transaction, before reading anything you intend to rewrite — taking it after the read defeats the point, since the stale read already happened. The lock is transaction-scoped, so COMMIT or ROLLBACK releases it and there is no unlock to forget. It serializes writers of the same parent while leaving different parents fully concurrent (proven in `tests/test_locks.py`).

It is **cooperative**: it only works if every writer of that collection takes it, and it is not a substitute for a constraint — reach for a constraint whenever the invariant fits in one row. Harmonix360 core has no such operation today; the helper ships as core infrastructure precisely so a fork adding its first ordered child collection reaches for this instead of inventing locking under deadline pressure.

---

## 3. API Conventions

- Base path: `/api/v1/`.
- All list endpoints support pagination (`limit`, `offset` or cursor — pick cursor if time allows, it's the more impressive choice).
- All mutating endpoints require an `Idempotency-Key` header for POST requests that create resources (bookings, transfers). Store recently-seen keys in Redis with TTL; return the cached response on replay. This is the "Idempotent APIs" skill — implement it as shared FastAPI middleware, not per-endpoint.
- All endpoints rate-limited server-side via a Redis token-bucket dependency (`app/core/rate_limit.py`), independent of any AI-specific rate limiting.
- Every mutating action writes one row to `audit_log` with: actor, action, entity, before/after diff (JSONB), timestamp. Non-negotiable — no mutation without an audit row.
- Errors follow a single envelope shape: `{"error": {"code": "...", "message": "...", "details": {...}}}`. No ad-hoc error formats per router.

---

## 4. Auth

- JWT access token (short-lived, ~15 min) + refresh token (long-lived, stored hashed in DB, rotated on use).
- Role-Based Access Control: `role` claim in JWT, enforced via a FastAPI dependency (`require_role("admin")`) on protected routes.
- Passwords hashed with bcrypt via `passlib`.
- No sessions in Postgres for auth state — Redis only, for refresh-token blacklisting on logout.

---

## 5. AI Layer — Provider Router

**File:** `app/ai/provider_router.py`

Requirements:
1. Interface: `async def generate(prompt: str, context: dict, task_type: str) -> AIResponse`.
2. Try Groq first. On `429` or timeout, fall back to Cerebras. On all cloud providers failing, degrade gracefully to `PENDING_REVIEW` for human oversight (rationale: 2-tier cloud provider chain + deterministic human escalation provides complete reliability without heavy local container footprint for Ollama).
3. Every call is queued through **Taskiq**, not called synchronously from a request handler. Routers enqueue a job (`await task.kiq(...)`) and return `202 Accepted` with a job ID; the frontend polls or listens via WebSocket for the result. Use a Taskiq middleware to auto-write the audit log entry around every AI job, rather than repeating that call inside each task.
4. Before calling any provider, check Redis cache: key = hash of `(prompt, context, task_type)`. Cache hits skip the provider entirely. TTL configurable per task type.
5. Server-side rate limiter (separate token bucket per provider) prevents the app from ever sending more than the provider's published RPM — fail fast to the fallback provider instead of hitting a 429.
6. Log every AI call (provider used, latency, token count, cache hit/miss) to the observability stack as a custom OTel span — this becomes a dashboard panel.

**AI Decision Nodes** (`app/ai/decision_nodes.py`): used inside the workflow engine. An AI decision node only ever *proposes* a transition (`approve` / `escalate` / `reject`) with a written rationale string, setting the transfer state to `PENDING_REVIEW` (rationale: AI nodes provide advisory recommendations; human override via `/transfers/{public_id}/override` is strictly enforced for all final state transitions per enterprise compliance standards). The rationale and the AI's raw output are written to the immutable audit log alongside the human override. A human must always approve or reject; the override itself is also audit-logged. This is a headline feature — build it once, generically, and reuse it across every workflow, not per-entity.

---

## 6. MCP Server

**File:** `app/mcp/server.py`
**Library:** `FastMCP` (the decorator-based, actively maintained standard — `pip install fastmcp`). Do not use the low-level `mcp` SDK directly (too much boilerplate for the timeline) and do not use `fastapi_mcp` (unmaintained since mid-2025).

- Expose Harmonix360's core domain actions (create booking, check asset status, run report, approve transfer, query audit trail, semantic asset search) as MCP tools using `@mcp.tool()` decorators. Do NOT auto-generate tools from the OpenAPI spec (`FastMCP.from_fastapi()`) — auto-converted tools perform worse for LLM callers than hand-curated ones, per FastMCP's own guidance. Hand-write each tool with a clear docstring and typed arguments.
- Run FastMCP as a **separate process** from the main FastAPI app (simpler to reason about and demo independently) using Streamable HTTP transport, not stdio — it needs to be reachable by remote MCP clients during the demo.
- Every MCP tool call goes through the exact same service layer as the REST API — no duplicated business logic. The MCP server is a second transport on top of the same services, not a parallel implementation.
- Every MCP tool call is audit-logged with `actor = "ai-agent"` and the calling agent's identity if available.
- Add one pgvector-backed MCP tool: `semantic_search_assets(query: str)` — this is your "ask your ERP a question in plain English" demo moment, and it doubles as your pgvector usage.
- Auth: MCP tool calls require a scoped API key, not a full user JWT — implement a separate, narrower permission model for agent access.

---

## 7. Observability

1. Instrument the FastAPI app with the OpenTelemetry Python SDK at startup (`app/core/telemetry.py`) — auto-instrument FastAPI, SQLAlchemy, Redis, and httpx (for outbound AI provider calls).
2. Export via OTLP to a local **SigNoz** instance (`observability/docker-compose.signoz.yml`), which gives metrics + traces + logs in one UI without standing up Prometheus/Grafana/Loki as three separate services.
3. Add **Sentry** (Python SDK) for exception tracking — near-zero setup, gives an instant stack-trace-with-breadcrumbs demo moment.
4. Deliberately build one "resilience demo": a script or feature flag that kills the primary AI provider mid-demo and shows the fallback + a live SigNoz dashboard panel reacting. Rehearse this — it is the single highest-value 30 seconds of the presentation.
5. Custom spans required (minimum): AI provider calls, workflow state transitions, booking-conflict-constraint rejections.

---

## 8. Docker Compose — Full Service List

`docker-compose.yml` must define, at minimum:
- `postgres` (already exists — extend, do not replace)
- `redis`
- `backend` (FastAPI, built from `backend/Dockerfile`)
- `frontend` (React + Vite, built from `frontend/Dockerfile` — served via a lightweight static server like `serve` or `nginx` in production mode; Vite dev server directly in local/dev compose profile)
- `worker` (Taskiq worker process, same image as backend, different entrypoint command — `taskiq worker app.jobs.broker:broker`)
- `mcp-server` (FastMCP process, same image as backend, different entrypoint — runs `app/mcp/server.py` on its own port)
- `ollama` (local fallback model — pull a small quantized model, e.g. `llama3.2:3b`, at container start)
- `otel-collector`
- `signoz` stack (can reference `observability/docker-compose.signoz.yml` via `include` or run as a separate compose file — do not merge into the main file if it causes bloat/slow local `up`)

All services on one Docker network. Backend and worker share the same codebase image to avoid drift.

---

## 9. Environment Variables (`.env.example` must contain all of these)

```
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@postgres:5432/harmonix360
HASHID_SALT=

# Redis
REDIS_URL=redis://redis:6379/0

# Auth
JWT_SECRET=
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=7

# AI Providers
GROQ_API_KEY=
CEREBRAS_API_KEY=
OLLAMA_BASE_URL=http://ollama:11434

# Observability
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
SENTRY_DSN=

# MCP
MCP_AGENT_API_KEY=
MCP_SERVER_PORT=8100
```

---

## 10. Four-Week Build Plan

### Week 1 — Foundation rebuild on FastAPI
- Scaffold `backend/` per Section 1 structure.
- Migrate schema design from prior Express prototype into SQLAlchemy models + Alembic migrations (do not port Express code — rewrite against this structure).
- Implement auth (JWT, RBAC), repository pattern, service layer for one full vertical slice (e.g. Assets CRUD) end-to-end as the template other entities copy.
- Set up `docker-compose.yml` core services (postgres, redis, backend, frontend).
- Implement hashid PK strategy on at least one table.
- **Deliverable:** one working end-to-end CRUD flow (Assets) with auth, audit logging, and hashid IDs, running in Docker.

### Week 2 — Workflow engine, jobs, MCP
- Build the generic workflow/state-machine engine (not per-entity, one reusable engine).
- Implement Taskiq worker + at least one background job (e.g. notification dispatch).
- Build the AI provider router (Groq/Cerebras/Ollama fallback) with Redis caching and rate limiting.
- Build the MCP server exposing 3–5 core tools, backed by the same service layer.
- **Deliverable:** a workflow with at least one AI decision node, callable both via REST and via an MCP tool call, fully audit-logged.

### Week 3 — Observability, realtime, remaining domain features
- Stand up SigNoz + Sentry, instrument all core paths.
- Build WebSocket realtime layer (native FastAPI WebSockets) for live updates (booking status, notifications).
- Add pgvector semantic search tool.
- Finish remaining AssetFlow domain features (allocations, transfers, maintenance, KPI dashboards, reporting).
- Build and rehearse the "kill the primary AI provider" resilience demo.
- **Deliverable:** feature-complete AssetFlow reference app with full observability and one rehearsed failure-recovery demo.

### Week 4 — Rehearsal, hardening, generation drill
- Timed dry run: generate a *new* domain app from Harmonix360 skills against a fake problem statement, end-to-end, on a clock, simulating production rollout.
- Load-test critical endpoints; verify rate limiter and AI fallback behave correctly under load.
- Prepare architecture-defense talking points for every major decision (hashid PKs, MCP layer, AI decision nodes, resilience demo).
- Mock jury Q&A session with people who push back.
- **Deliverable:** team can generate a new ERP vertical from Harmonix360 in a rehearsed, timed window, with all observability and AI resilience features working live.

---

## 11. Hard Constraints for Agents (Do Not Violate)

1. Do not introduce Express, Node backend services, Next.js, BullMQ, Celery, or Socket.IO. FastAPI + Taskiq + native WebSockets + React/Vite only.
2. Do not put business logic in router files. Routers call services; services call repositories.
3. Do not expose integer primary keys in any API response, log shown to a client, or WebSocket payload.
4. Do not call an AI provider synchronously from a request handler — always enqueue via Taskiq.
5. Do not skip the audit log on any mutating action, including AI-driven ones.
6. Do not build a second, parallel implementation of business logic for the MCP server — it must reuse the service layer.
7. Do not auto-generate MCP tools from the OpenAPI spec — hand-write each `@mcp.tool()` with a clear docstring.
8. Do not merge SigNoz/observability compose files into the main `docker-compose.yml` if it materially slows local `docker compose up` — keep it composable.
9. Any deviation from this document requires updating this document first, in the same PR/session, with a one-line rationale.

---

## 12. Definition of Done (pre-hackathon)

- [ ] Full AssetFlow app runs via `docker compose up` with zero manual steps beyond `.env` population.
- [ ] AI provider fallback chain (Groq → Cerebras → Ollama) verified working by manually killing each provider in turn.
- [ ] MCP server tools callable from an external MCP client (e.g. Claude) and produce audit log entries.
- [ ] SigNoz dashboard shows live traces/metrics for AI calls and workflow transitions during a demo run.
- [ ] Sentry captures a deliberately triggered error with full breadcrumbs.
- [ ] Booking double-book attempt via concurrent requests correctly rejected by the Postgres `EXCLUDE` constraint, not just app logic.
- [ ] Team can state, from memory, the trade-off rationale for hashid PKs, dropping RLS/multi-tenancy, the MCP layer, and AI decision nodes.
- [ ] Full generation drill (Week 4) completed at least twice.

---

## 13. Deviation Log (per Section 11, rule 9)

| Date | Deviation | One-line rationale |
|---|---|---|
| 2026-08-07 | `ResourceBooking` generalized from Asset-only (`asset_id` FK) to polymorphic (`resource_type`/`resource_id`) with a resource registry, plus a minimal `MeetingRoom` proof entity and a new `/api/v1/bookings` router — migration `005_generic_resource_booking`. | Built **ahead of**, not in response to, a new-domain dry run: no Week 4 generation drill has run yet (`ValidationProjects/` contains only AssetFlow, the source domain), so the concrete need was *assumed* rather than demonstrated — accepted as a pre-freeze bet that booking is the most likely cross-domain reuse point, and reversible via the migration's tested `downgrade()`. |
| 2026-08-10 | Section 2 gains rules 8, 9, 10; new Section 2a (advisory locks). Migrations `006`–`008`: `EXCLUDE` predicate, `version` column dropped, `assets.purchase_cost` → `numeric(12,2)`. New `app/core/locks.py` + `tests/test_locks.py`. | Correctness pass driven by a **real** signal for once — a jury review of the GlobeTrotter fork surfaced bugs that originated here and were inherited at fork time, so each rule is written back against a defect that actually shipped rather than an anticipated one. See 13.2. |
| 2026-09-03 | Real optimistic concurrency (rule 10's TODO) delivered as `version_id_col` on `AuditedEntity`; new `/sync/pull` + `/sync/push` endpoints, `sync_mutations` idempotency log, `app/services/sync_registry.py`. Migrations `013`–`014`. New offline-capable frontend layer (IndexedDB outbox/cache, conflict UI). | Deliberate, scoped unfreeze for offline support — the Odoo hackathon judges explicitly evaluate cloud-only vs. offline-capable solutions, and rule 10's deferred OCC is the direct prerequisite genuine sync conflict detection needs, so this reuses it rather than building a second mechanism. See 13.3. |

### 13.1 Freeze Record — 2026-08-07 (Harmonix360 core re-frozen)

Core was briefly unfrozen for the pre-hackathon hardening pass, then re-frozen at this state:

- **CI**: `.github/workflows/ci.yml` — Python 3.12 pinned, Postgres 16 +
  Redis 7 service containers, `alembic upgrade head`, then `pytest -v`.
  Ruff/black run report-only (pre-existing debt, no ruff config yet).
- **Generic `ResourceBooking`**: `asset_id` → `resource_type`/`resource_id`,
  Postgres `EXCLUDE` constraint rebuilt on the generic columns, bookable
  resources resolved through `app/services/resource_registry.py`. Migration
  `005_generic_resource_booking` (upgrade non-destructive; downgrade lossy only
  for non-asset bookings). New `/api/v1/bookings` router; MCP `create_booking`
  tool signature updated to match.
- **Smoke suite**: `tests/test_smoke.py` — 7 tests (health, BaseRepository
  round-trip, booking 201, overlap 409, unknown type 400, missing 404,
  non-bookable 400). Verified to actually fail when the 409 translation is
  broken, so the CI test step is a real tripwire rather than a no-op.
- **Deviation logged**: see the `ResourceBooking` row in the table above —
  the generalization was built **ahead of** a new-domain dry run, not in
  response to one — no Week 4 generation drill has run yet.

Known gap carried into freeze: no ruff/black config exists, so lint is
advisory only; 338 findings are pre-existing house-style mismatches.

**No further core work until the Week 4 (PS) dry run surfaces something concrete.**

### 13.2 Hardening Pass — 2026-08-10 (fork-feedback correctness pass)

Core was unfrozen a second time. The trigger was the concrete signal 13.1 said
to wait for: a jury review of GlobeTrotter (built on a fork of this codebase)
surfaced defects that had originated **in Harmonix360** and were inherited at fork
time. Scope was limited to those defects plus the reusable patterns that stop
the same class recurring in the next fork.

Fixed:

- **`resource_bookings_range_overlap_excl` had no `WHERE` predicate**
  (`010_booking_exclusion_predicate`). Cancelled AND soft-deleted bookings still
  reserved their time range; a soft-deleted booking blocked a slot no reachable
  row could explain or release. Now
  `WHERE (deleted_at IS NULL AND status <> 'CANCELLED')`. Codified as Section 2
  rule 8. Side effect worth knowing: `BookingService.override_booking`'s
  `23P01` handler was dead code before this — a `CANCELLED` row already held its
  range, so re-confirming it could not conflict. It is live now.
- **`version` column dropped from all 11 audited tables**
  (`011_drop_decorative_version`). Nothing read, incremented or compared it;
  verified `count(*) FILTER (WHERE version <> 1) = 0` on every table before
  dropping. Optimistic locking deliberately NOT implemented — see the TODO in
  Section 2 rule 10.
- **`assets.purchase_cost` `double precision` → `numeric(12,2)`**
  (`012_money_as_numeric`), the only monetary column in the schema. Codified as
  Section 2 rule 9 so the next domain's first migration gets it right.

Added:

- **`app/core/locks.py`** — `acquire_entity_lock()`. Harmonix360 has no
  bulk-resequencing operation of its own, so it ships with an isolated
  concurrency test (`tests/test_locks.py`) rather than a fabricated call site.
  Documented as Section 2a.
- **Constraint-translation contract on `BaseService`** (docstring). Harmonix360 has
  no `DEFERRABLE` constraints, so the inconsistency the jury found in the fork
  has no direct analogue here — but the same *shape* does: translating `23P01`
  is an opt-in call-site convention, honoured in both of `BookingService`'s
  mutation paths and absent from the generic `AIReviewJob.run`. That gap is
  currently unreachable (no AI decision moves a booking into the constrained
  set) and its failure mode is safe (the job fails, the row keeps its prior
  status), so it was documented rather than papered over with an invented
  translation.

Verified unchanged:

- **Idempotency-Key already covers the AI-job-creating endpoint.**
  `POST /api/v1/transfers/` goes through the same global middleware as Week 1's
  asset creation; a replay returns the byte-identical cached response and
  enqueues no second job (`verify_idempotency_ai_job.py`). No code changed.
- **No plaintext token cache exists to migrate to an OS keychain.** Harmonix360
  issues access tokens only, persists no token anywhere, and MCP auth is a
  static env-var API key. Refresh tokens, logout, and the Redis blacklist
  described in Section 4 are **not implemented** — building them is a new
  subsystem, out of scope for a correctness pass.

**OPEN ITEMS — deliberately deferred, do not lose these**

These were found during the pass, judged out of its scope, and left untouched on
purpose. Each is a decision someone has to make, not a task waiting to be done.

1. **Harmonix360 and the GlobeTrotter fork share one Postgres container.** The
   container's `harmonix360` database is stamped `006_globetrotter_domain` — a
   revision that does not exist in this repo — and carries the fork's `trips`,
   `cities`, `activities`, `trip_stops`, `trip_activities` tables on top of
   Harmonix360's. This repo's alembic history cannot be applied to it. Harmonix360 now
   uses its own **`harmonix360_core`** database in the same container, and `.env`
   points there. Still open: whether the two projects should share a container
   at all, and whether the fork should be repointed or given its own. Related:
   core's post-fork migrations were renumbered to start at 010 so the two number
   sequences stay unambiguous (see `010_booking_exclusion_predicate`'s
   docstring) — that is cosmetic only; both chains still branch from 005 and a
   merged repo would need an explicit `alembic merge`.

2. **`alembic downgrade base` fails at 001.** `001_initial_schema.downgrade()`
   drops `users` before `departments`, but `fk_departments_head_user` still
   depends on it → `DependentObjectsStillExistError`. Pre-existing, unrelated to
   this pass. Because DDL is transactional the whole downgrade rolls back and
   the database is left safely at head, so nothing is at risk day to day; only a
   full teardown is blocked. One-line fix when someone wants it: add
   `op.drop_constraint('fk_departments_head_user', 'departments',
   type_='foreignkey')` at the top of that `downgrade()`. Not applied here
   because editing the initial migration deserves its own deliberate change,
   not a drive-by in a correctness pass.

Known gaps carried forward: ruff/black still advisory (unchanged from 13.1);
Section 4's refresh-token/blacklist design remains aspirational, not built.

### 13.3 Offline Sync Pass — 2026-09-03

**Trigger.** Odoo's hackathon judges explicitly evaluate whether a solution
"relies entirely on cloud/internet" vs. plans for offline/local operation.
Harmonix360 had no offline story at all. Building real offline sync needs real
conflict detection, and rule 10 (Section 2) already named the fix and left it
as a deliberately deferred TODO — this pass is that TODO getting built,
triggered by a concrete external requirement rather than invented ahead of one
(contrast with the `ResourceBooking` generalization in the first deviation-log
row, which *was* built ahead of a demonstrated need).

**Scope boundary.** Single-server outbox sync — one Postgres, one backend, a
client-held cursor and a client-held outbox — not distributed replication, not
CRDTs, not peer-to-peer merge. The server is the single source of truth;
conflicts are detected (version mismatch) and *surfaced*, never silently
resolved. Deliberately out of scope for this pass: extending explicit
`known_version` negotiation to the plain REST endpoints (`transition_asset`,
`override_booking`, `human_override` still get real OCC as a free side effect
of `version_id_col`, just not a documented conflict-resolution *flow* — no
caller threads a version through them today); background-job/AI-driven
mutations similarly get the free safety net but no sync semantics, since
they're not offline-client-initiated.

**Real optimistic concurrency (rule 10's TODO, delivered as specified there).**
`app/models/mixins.py`'s `AuditedEntity` gets a real `version: Mapped[int]`
plus `__mapper_args__["version_id_col"]` — exactly the mechanism rule 10 named
as the correct one, not a hand-rolled `WHERE version = ...`. This means every
existing call site through `BaseRepository.update`/`soft_delete` (asset
transitions, booking overrides, transfer human-overrides, notes) gets a real
concurrency guard for free, with zero code changes at those call sites.
`BaseRepository` detects a conflict with a cheap pre-flush read-and-compare
(not by waiting for a flush failure) so the common case never needs a
mid-transaction rollback — load-bearing for the sync-push batch, where nine
sibling mutations still need the same session/transaction afterward.
`StaleDataError` from the ORM's own flush-time check remains as defense in
depth for the sub-millisecond gap between the pre-check and the flush, with a
best-effort (not re-queried) conflict payload, since that path can't safely
re-query without a rollback it doesn't own. Migration `013_add_optimistic_version`
scopes the new column to the five tables that actually mix in `AuditedEntity`
today (`assets`, `transfer_requests`, `resource_bookings`, `meeting_rooms`,
`notes`) — not the eleven migration `011` dropped it from — using the same
"enumerate from the schema, don't guess" methodology `011` used.

**Server-authoritative time.** `updated_at`'s `onupdate` moved from a Python
callable (`datetime.now(timezone.utc)`, computed per-worker) to a Postgres
expression (`app/core/clock.py:server_utc_now()`, `timezone('UTC',
timezone('UTC', now()))` — a double round-trip that re-interprets `now()`'s
naive-UTC wall-clock reading as UTC again, making it immune to a connection's
session `TimeZone` setting). This was a specific operational requirement for
this pass: the pull cursor orders rows by `(updated_at, id)`, and if
`updated_at` were assigned by whichever app-tier worker handled a given
request, clock drift between workers could put rows in an order the cursor
comparison gets wrong. `created_at` was already `server_default=now()` at the
DDL level from each table's creating migration — the fix there was removing
the redundant Python-side default that had been silently overriding it in
every INSERT since 001, not a schema change. `SyncMutation.created_at` uses
the same expression.

**Cursor design.** Opaque base64 token wrapping a per-entity-type map,
`{entity_type: [updated_at, id]}` — chosen over a single global monotonic
sequence to avoid a second schema mechanism competing with the mutation log
(`sync_mutations`) already being added for idempotency; each entity type's
delta query is independent, which is also what makes a new syncable entity
just one more key in the map. `id` breaks ties on identical `updated_at`.
Known, accepted trade-off: `updated_at` is assigned at flush time, not commit
time, so two concurrent transactions can commit out of order relative to their
own timestamps — a pull that already advanced past a later timestamp could
otherwise permanently miss a row that commits afterward with an earlier one.
Every pull query adds `AND updated_at <= server_utc_now() - INTERVAL '2
seconds'` (`SYNC_SAFETY_LAG_SECONDS`, `app/services/sync.py`) to bound this: a
row is only ever handed to a client once it's older than the lag window, which
is comfortably longer than any single request's transaction lifetime here.
This is a documented, bounded trade-off for a single-server deployment, not a
silent gap — a distributed/multi-writer deployment would need a different
mechanism (out of this pass's scope boundary above).

**Idempotency.** New `sync_mutations` table, unique on `(actor_key,
client_mutation_id)`, checked *inside* the same transaction that applies each
mutation — deliberately not relying solely on the existing `Idempotency-Key`
header middleware (`app/middleware/idempotency.py`), which caches one response
per header value for an entire batch and can't distinguish "replay this one
op" from "this op happens to be in a batch whose header I've cached before".
Each op in a push batch runs its own attempt so one rejected/conflicting op
can't poison the rest of the batch.

**Generic registry.** `app/services/sync_registry.py` mirrors
`resource_registry.py`'s dict-of-callables shape exactly — a `SyncableEntity`
(model, service factory, create/update schemas, a serializer). Registered in
`app/services/sync_entities.py` (imported for its side effect, same pattern as
`booking_resources.py`): `note` and `asset`, proving genericity across two
entities without inventing new domain surface. `app/services/sync.py` and
`app/api/v1/routers/sync.py` contain zero Note/Asset-specific code.

**Frontend.** New `idb`-based layer (`src/lib/offline-db.ts`: `cache`,
`outbox`, `conflicts`, `meta` object stores), a dual reachability check
(`navigator.onLine` AND a live `/health` ping — `src/lib/reachability.ts`), a
generic `useOfflineMutation`/`useOfflineEntities` pair
(`src/hooks/useOfflineMutation.ts`) used by a new Notes page
(`src/routes/notes/NotesPage.tsx`, reusing Notes as the reference entity
`app/verify_scaffolding.py` already established for generic-scaffolding
proofs), and a minimal conflict-resolution modal. Every write — online or
offline — goes through the same `POST /sync/push` path; the only difference
online makes is how soon the outbox gets flushed, not which code runs.

**Bugs this pass's live-verification requirement actually caught** (each
would have shipped silently under a "should work" standard):
- TanStack Query's default `networkMode: 'online'` pauses ALL queries the
  instant `navigator.onLine` goes false — including `useOfflineEntities`,
  whose queryFn only reads IndexedDB and has no actual network dependency.
  Without `networkMode: 'always'`, the UI would freeze on stale data the
  moment you go offline, silently defeating the entire feature.
- `BaseRepository.create()`'s existing "flush to get the id, then set the
  real hashid public_id" two-step (predates this pass) became a second,
  version-bumping UPDATE the instant `version_id_col` went live — every new
  row would have started at version 2, not 1. Fixed with a Core-level UPDATE
  that bypasses the ORM's version check for that one same-transaction,
  nothing-can-race-it fixup (`set_committed_value` keeps the ORM's dirty-
  tracking honest afterward).
- A conflict's "current state" was read via a plain `select()`, which returns
  whatever object is already in that session's identity map — the caller's
  own stale, uncommitted copy — rather than the just-committed row that
  actually won. Fixed with `populate_existing=True` (`get_by_id_fresh`).
- Chaining an offline CREATE and an offline UPDATE of the same not-yet-synced
  row in one outbox produced an UPDATE still pointing at the CREATE's local
  placeholder id (`local:<uuid>`) once both got pushed together — the server
  correctly reported `NOT_FOUND`, but logging that outcome then hit a genuine
  second bug (`sync_mutations.entity_id` is `VARCHAR(32)`, the placeholder
  isn't) and 500'd instead. Fixed on both ends: `pushOutbox` now resolves
  same-batch chained dependencies across rounds (push what doesn't depend on
  an unresolved local id, learn the real ids from what applied, rewrite
  dependent outbox entries, repeat), and `_log_mutation` defensively
  truncates an oversized `entity_id` rather than letting a log write 500 an
  otherwise-correctly-classified outcome.
- A conflict modal `data-testid` was placed on `DialogContent`
  (`src/components/ui/dialog.tsx`), which only forwards `className`/
  `children` and drops other props — the modal rendered (visible in the DOM,
  confirmed via raw HTML) but was unfindable by any test relying on that
  attribute. Moved the test id to an inner `div` the component controls.

**Environment finding, unrelated to the feature but discovered while building
it:** the shared Postgres container's `harmonix360_core` database — named in
13.2 as Harmonix360's own, unshared database — is now *also* stamped at alembic
revision `015_booking_hardening` (not a revision in this repo) and carries
`courts`/`venues`/`resources`/`reviews`/`time_slot_blocks`/`outbox_events`/
`idempotency_keys` tables belonging to an unrelated project. A second
collision on the same container, on top of the GlobeTrotter one 13.2 already
logged. `alembic upgrade head` against it fails closed rather than
corrupting anything (its version doesn't chain to this repo's history), which
is how this was caught before anything was touched. This pass's work was done
against a new, isolated `harmonix360_dev` database instead (migrated 001 through
014 fresh) — `harmonix360_core` and `harmonix360` were left exactly as found.
`.env`'s `DATABASE_URL`/`MIGRATION_DATABASE_URL` now point at `harmonix360_dev`.
Reclaiming or repointing the shared container remains an open decision, same
status as the GlobeTrotter finding in 13.2 — not resolved here.

**Verification.** `verify_offline_sync.py` (repo root) — a hybrid Playwright +
`requests` script, not a description of expected behavior. Run with the
backend on `:8000` (`uvicorn app.main:app`, from `harmonix360/backend`, repo-root
`.env` loaded), the frontend on `:3000` (`npm run dev`, from
`harmonix360/frontend`), and `harmonix360_dev` migrated to head. It proves, against
the real running stack: (a) an offline create and update land in real
IndexedDB (`cache`/`outbox`, dumped raw); (b) push/pull reconciliation matches
the server's row exactly, not a stub; (c) a live conflict — Client A edits
online while Client B edits the same row offline — reproduces an explicit
409-shaped payload with the server's actual diff, the row is never silently
overwritten, and the conflict modal really renders in the browser; (d) an
identical `client_mutation_id` submitted twice returns a byte-identical
result, with the database queried directly afterward to confirm exactly one
row and no version re-increment.
