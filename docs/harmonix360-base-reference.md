> **ARCHIVED — platform foundation reference.**
>
> This is the README of the Harmonix360 platform framework as it stood BEFORE
> the PeoplePay360 HR & Payroll domain was built on top of it. It is kept
> because it is the clearest description of the domain-agnostic machinery
> PeoplePay360 inherits — the repository/service base classes, the immutable
> audit log, the `EXCLUDE`/advisory-lock/optimistic-concurrency patterns, the
> `Idempotency-Key` middleware, the AI provider router, the FastMCP tooling and
> the offline-sync engine. All of that is still in the tree and still in use.
>
> **What it says about the DOMAIN is out of date.** The AssetFlow entities it
> mentions (Asset, TransferRequest, ResourceBooking, MeetingRoom, Notes) were
> deleted in Phase 0 as wrong-domain content, along with the generic
> state-machine workflow engine. Where this file and
> [`02_SYSTEM_ARCHITECTURE.md`](../02_SYSTEM_ARCHITECTURE.md) disagree, the
> architecture document wins.
>
> For how to run and develop PeoplePay360 today, see the root
> [README.md](../README.md).

---

# Harmonix360

A modular backend architecture and platform infrastructure framework powering enterprise services. Harmonix360 provides a hardened set of foundational patterns: generic repository/service layers, immutable audit logging, AI-assisted decision workflows, FastMCP agent tooling, telemetry/observability, and an offline-first synchronization engine with bare frontend tooling.

> **Source of truth for architecture decisions:** [`HARMONIX360_ARCHITECTURE.md`](./HARMONIX360_ARCHITECTURE.md). Any architectural decisions, conventions, and design specifications are maintained there.

---

## Architectural Philosophy

Harmonix360 is designed with strict separation of concerns and layered domain boundaries:

- **Modular Backend Core:** Entity → Repository → Service → Router → Validation → Audit → Workflow → RBAC. Base classes (`BaseRepository[T]`, `BaseService`) provide generic CRUD, ID conversion (Hashids), and lifecycle handling, ensuring domain entities require minimal boilerplate.
- **AI-Assisted Workflow Engine:** Decoupled decision nodes evaluate requests against pluggable prompts and fallback hierarchies (Groq primary → Cerebras fallback). Decisions land in human-in-the-loop review states rather than unmonitored execution.
- **FastMCP Agent Tooling:** Standardized ASGI-mounted FastMCP server exposing authenticated domain tools directly to AI coding agents and autonomous workflows.
- **Offline Sync & Optimistic Concurrency:** IndexedDB client store paired with backend mutation logs, version checks, and conflict resolution mechanisms.
- **Bare Frontend Foundation:** Pre-configured React 19 + Vite + TypeScript + Tailwind + Shadcn UI application shell ready for domain modules.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI (Python 3.12), SQLAlchemy 2.0 async, Alembic, Pydantic v2 |
| Database | PostgreSQL 16, Redis 7 |
| Background Jobs | Taskiq (async-native with Redis broker) |
| Realtime | Native FastAPI WebSockets |
| Frontend | React 19 + Vite + TS + React Router v7 + TanStack Query + Shadcn UI / Tailwind |
| AI Inference | Multi-tier router (Groq primary → Cerebras fallback) |
| AI Orchestration | FastMCP, dedicated service process on port 8100 |
| Observability | OpenTelemetry SDK → SigNoz + Sentry |
| Public Identifiers | Hashids, namespaced per entity (`ast_`, `trf_`, `bkg_`, `note_`) |

---

## Getting Started

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) & Docker Compose (recommended)
- Python 3.12+ (if running backend locally outside Docker)
- Node.js 20+ (if running frontend locally outside Docker)
- [`uv`](https://docs.astral.sh/uv/) or `pip`

### Option A — Docker Compose (Recommended)

```bash
git clone <repo-url> harmonix360
cd harmonix360

# 1. Configure environment
cp .env.example .env
# Edit .env and fill in GROQ_API_KEY / CEREBRAS_API_KEY / SENTRY_DSN as needed

# 2. Build and start all services (postgres, redis, backend, worker, mcp-server, frontend)
docker compose up --build

# 3. In a second terminal, execute database migrations
docker compose exec backend alembic upgrade head

# 4. (Optional) Seed demo user and initial reference data
docker compose exec backend python -m app.seed
```

Services once started:

| Service | URL |
|---|---|
| Backend API (Swagger UI at `/docs`) | http://localhost:8000 |
| FastMCP Server | http://localhost:8100 |
| Frontend App | http://localhost:3000 |
| PostgreSQL | `localhost:5432` (or `5544` if using local override) |
| Redis | `localhost:6379` (or `6380` if using local override) |

### Option B — Manual Local Development

**Backend**

```bash
cd harmonix360/backend

# Start datastores via Docker
docker compose -f ../../docker-compose.yml up postgres redis -d

# Virtual environment setup
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS/Linux

# Install dependencies using uv or pip
uv sync
# Or: pip install -r requirements.txt && pip install -r requirements-dev.txt

# Run migrations and seed data
alembic upgrade head
python -m app.seed

# Start backend server
uvicorn app.main:app --reload --port 8000
```

Start the background worker and FastMCP server in separate terminals:

```bash
taskiq worker app.jobs.broker:broker app.jobs.tasks.notifications app.jobs.tasks.ai_jobs app.jobs.tasks.transfer_decision app.jobs.tasks.booking_decision
python -m app.mcp.server
```

**Frontend**

```bash
cd harmonix360/frontend
npm install
npm run dev
```

The frontend runs at `http://localhost:3000` and proxies API requests to `http://localhost:8000`.

### Running Tests & Verification

```bash
cd harmonix360/backend
pytest -v
```

Additional standalone proof and resilience scripts:

```bash
docker exec -i harmonix360_postgres psql -U postgres -d harmonix360_core -f - < verify_booking_exclusion.sql
python verify_money_decimal.py          # Verifies exact Decimal precision end-to-end
python verify_idempotency_ai_job.py     # Verifies Idempotency-Key guarantees
python -m app.verify_scaffolding        # Verifies generic BaseRepository / BaseService
```

---

## Core Platform Architecture

### Generic Base Layers
`app/repositories/base.py`, `app/services/base.py`, `app/models/mixins.py`
- `BaseRepository[T]` and `BaseService` encapsulate robust CRUD operations (`get_by_id`, `get_by_public_id`, `list`, `create`, `update`, `soft_delete`), enforcing uniform hashid encoding and decoding.
- Adding a new entity requires minimal domain-specific code while inheriting query optimizations and audit tracking.

### Hashid Namespacing
Entities employ distinct, type-specific salt namespaces and public prefixes (`ast_` for assets, `trf_` for transfers, `bkg_` for bookings, `note_` for notes), preventing ID confusion across endpoints.

### Audit & Compliance
`app/audit/logger.py`, `app/jobs/audit_middleware.py`
- Every state mutation and asynchronous Taskiq job is automatically logged to an append-only audit trail.
- Database-level permissions strictly prevent `UPDATE` or `DELETE` operations on audit tables.

### AI Decision Workflows
`app/ai/decision_nodes.py`, `app/ai/review_job.py`
- `AIDecisionNode` executes prompt evaluation with strict fallback chains and structured schema validation.
- All AI evaluation outputs route to a pending review state for administrative verification.

### FastMCP Tooling Layer
`app/mcp/tool_wrapper.py`
- `@mcp_tool(mcp)` decorator provides API key validation, session management, database transaction scoping, and structured error responses.

### Multi-Tier AI Provider Routing
`app/ai/provider_router.py`
- Dual-tier failover routing across Groq and Cerebras ensures high availability with graceful degradation.

---

## Repository Structure

```
.
├── harmonix360/
│   ├── backend/               # FastAPI backend — repositories, services, routers, AI, MCP, jobs
│   ├── frontend/              # React + Vite application shell & UI components
│   └── observability/         # Observability configurations and collectors
├── .github/workflows/         # CI/CD workflows
├── docs/                      # Reference specifications and workflow docs
├── HARMONIX360_ARCHITECTURE.md # Master architectural roadmap & design record
├── OFFLINE_SYNC_IMPLEMENTATION.md # Offline sync architecture and contract
├── AI_PLAYBOOK.md             # Platform engineering constitution & AI coding guidelines
├── docker-compose.yml         # Container orchestration specification
└── verify.py                  # Integration verification suite
```

---

## Extending Harmonix360 with New Domains

To add a new domain entity to the platform:

1. **Define ORM Model:** Create the model in `app/models/` inheriting from `Base` and `AuditedEntity`.
2. **Generate Alembic Migration:** Create and review the migration in `alembic/versions/`.
3. **Define Pydantic Schemas:** Create request, response, and filter schemas in `app/schemas/`.
4. **Implement Repository:** Subclass `BaseRepository[Entity]` in `app/repositories/`.
5. **Implement Service:** Subclass `BaseService` in `app/services/`, wiring custom validation and business logic.
6. **Register API Router:** Add the router in `app/api/v1/routers/` and mount it in `app/main.py`.
7. **Expose MCP Tools:** Decorate domain actions with `@mcp_tool` in `app/mcp/server.py` to enable agent interactions.
