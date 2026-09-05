# PeoplePay360

HR and payroll, built around the one thing most HR tools get wrong: an employee
has many contracts over time, and payroll must resolve **exactly one** of them
for the period being run. Everything else here follows from taking that
seriously — the contract history, the schedule an employee actually works, the
attendance that needs correcting, the leave balances that move when a request
is approved, and the deterministic rules that turn all of it into an auditable
payslip.

Built on the **Harmonix360** platform foundation (this repository's own
backend framework): repository/service base classes, an append-only audit log,
optimistic concurrency, Postgres exclusion constraints, idempotency, background
jobs, AI provider routing, MCP agent tooling, offline sync and observability.

- **Scope:** [`01_PRD.md`](./01_PRD.md)
- **Design:** [`02_SYSTEM_ARCHITECTURE.md`](./02_SYSTEM_ARCHITECTURE.md) — the
  source of truth. Where this README and that document disagree, it wins.

---

## The two layers

PeoplePay360 is deliberately split in two, and the split is enforced by the
build order rather than by good intentions.

**The core** is the entire problem statement, and it is not optional:
Employee, Contract, Working Schedule, Attendance, Time Off, Salary Rules,
Payrun, Payslip, Dashboard, RBAC, PDF and email. Phases 1–7. It is built and
demoable end to end **first**.

**The differentiation layer** is additive, and it is built afterwards, on top of
a working core: AI explanation and investigation, MCP agent tooling, offline
attendance and leave sync, realtime updates, payslip explainability, anomaly
detection, and observability. Phases 8–10.

The rule that governs every decision in this repository: the differentiation
layer runs through the **same** services, validation, RBAC and audit trail as
the core. It is never a parallel path, never a shortcut, and it never
substitutes for a core requirement. If the two ever compete for time, the core
wins.

Two consequences worth stating plainly, because they are what make the second
layer safe to have at all:

- **AI never calculates payroll.** The deterministic rule engine is the sole
  authority for every figure on a payslip. The AI layer has no code path that
  writes to `Payslip` or `PayslipLine` — it reads and narrates.
- **There is one path to the database.** A human clicking a button, an MCP tool
  call and an offline-sync push all terminate in the identical service method,
  with the identical role check and the identical audit write. There is no
  second implementation of any business rule.

---

## Running it

Requires Docker and Docker Compose. Nothing else — no Python, no Node, no
database on your machine.

### Core development (Phases 1–7)

```bash
git clone <this repo> && cd Odoo2026
cp .env.example .env      # optional; every value has a working default
docker compose up --build
```

Brings up postgres, redis, backend, worker and frontend. The backend migrates
the database, seeds demo data and starts serving, in that order — so a clean
clone is immediately usable rather than healthy-but-empty.

| | |
|---|---|
| Frontend | http://localhost:3000 |
| API | http://localhost:8000 |
| API docs | http://localhost:8000/docs |

No Groq or Cerebras key is needed. No MCP process, no collector. This is the
profile the core is developed and demoed against.

### Advanced (Phase 8+)

```bash
docker compose --profile advanced up --build
```

Everything above **plus** the MCP server (Streamable HTTP on
http://localhost:8100) and an OpenTelemetry collector (OTLP on 4317/4318). Set
`OTEL_ENABLED=true` to have the backend export traces to it, and put
`GROQ_API_KEY` / `CEREBRAS_API_KEY` in `.env` if you want the AI layer live —
without them it reports "unavailable" and never fabricates an answer.

Both commands are expected to work from a clean clone at every commit. The
second one is how we check that the platform layer still works, not just that
the core does.

### Demo logins

Seeded automatically, one per role, so the permission matrix can be
demonstrated by logging in rather than described:

| Email | Password | Role |
|---|---|---|
| `admin@peoplepay360.com` | `admin123` | Admin |
| `payroll.manager@peoplepay360.com` | `payroll123` | HR Payroll Manager |
| `payroll.user@peoplepay360.com` | `payroll123` | HR Payroll User |
| `hr.manager@peoplepay360.com` | `hrmanager123` | HR Manager |
| `employee@peoplepay360.com` | `employee123` | Employee |

Demo credentials, in version control on purpose. They are not a production
credential path.

---

## Working on the backend directly

Useful when you want a debugger or a fast test loop. Compose still supplies
Postgres and Redis.

```bash
docker compose up -d postgres redis
cd harmonix360/backend
uv sync --extra dev
uv run alembic upgrade head
uv run python -m app.seed
uv run pytest -v
uv run uvicorn app.main:app --reload
```

`.env` at the repository root carries the host-side URLs (`localhost`, not the
compose hostnames). `docker-compose.override.yml` is machine-local and
gitignored — use it if another Postgres or Redis already owns 5432/6379 on your
machine, and point `.env` at whatever ports it republishes.

The test suite runs against a **real** Postgres, never SQLite, and that is not
negotiable: the contract non-overlap constraint and the advisory lock are
properties of the database's constraint system and lock manager. Neither can be
exercised against SQLite, and neither can be proven by a mock.

---

## Five things worth knowing before you change anything

**Two active contracts can never overlap.** Enforced by a Postgres `EXCLUDE`
constraint, not by application code — two concurrent requests both pass a
select-then-insert check and both commit, so only the database can refuse.
Consequence: setting `status = 'active'` moves a row *into* the constrained set,
so a status-only `UPDATE` can raise `23P01` exactly as an `INSERT` can. Every
service method that can do that owes the 409 translation.

**Money is `Numeric(12,2)` and `Decimal`, everywhere, always.** No `float`
touches the payroll path, and amounts are stringified across every JSON
boundary. A binary float cannot represent `0.10`, so totals drift by cents
nobody can account for.

**Roles are checked server-side, on every path.** The frontend hides nav
entries a role cannot use; that is a courtesy. `require_role` is the control,
and it guards MCP tool calls and offline-sync pushes identically.

**`public_id` is the only identifier that leaves the server.** Prefixed hashids
(`emp_`, `ctr_`, `pslip_`…). The integer primary key is sequential — exposing it
would let anyone count the workforce and address rows they were never given.
The prefixes are load-bearing: they are what stops a payslip id resolving to an
employee row.

**The audit log is append-only at the database level.** `UPDATE` and `DELETE`
are revoked from the runtime role. A human action and an AI-confirmed action
leave equally inspectable rows; there is no quieter path.

---

## Layout

```
harmonix360/
  backend/
    app/
      models/       employee, contract, working_schedule, attendance,
                    time_off, salary, payroll  +  user, department, entities
      repositories/ BaseRepository subclasses, one per entity
      services/     business logic — the only path to the database
      api/v1/       routers, auth dependencies
      ai/           provider routing, cache, decision nodes   (dormant → Phase 9)
      mcp/          FastMCP server                            (dormant → Phase 9)
      realtime/     WebSocket manager                         (dormant → Phase 10)
      core/         config, security, locks, telemetry, redis
    alembic/        migrations, including the contract-overlap constraint
    tests/          real Postgres, no mocks for constraints
  frontend/         React 19 + Vite + Tailwind + Shadcn
  observability/    OpenTelemetry collector config
docs/
  harmonix360-base-reference.md   archived platform-framework README
```

Anything marked *dormant* is present, imports cleanly and is covered by tests,
but is wired to no HR domain surface yet. That is deliberate: it stays
retained, not deleted, so the phase that needs it adds registrations rather
than rebuilding infrastructure.
