# PeoplePay360 Backend

FastAPI + SQLAlchemy 2.0 async + Alembic, on the Harmonix360 platform
foundation. See the root [README.md](../../README.md) for how to run it and
[02_SYSTEM_ARCHITECTURE.md](../../02_SYSTEM_ARCHITECTURE.md) for the design.

## Layout

    app/models/        HR domain (employee, contract, working_schedule,
                       attendance, time_off, salary, payroll) plus the shared
                       user/department models and the platform tables
    app/repositories/  BaseRepository subclasses; hr.py holds the public_id
                       prefix table from Architecture 2
    app/services/      business logic — the only path to the database
    app/api/v1/        routers and the require_role auth dependencies
    app/ai/            provider routing, cache, decision nodes (Phase 9)
    app/mcp/           FastMCP server, separate process        (Phase 9)
    app/realtime/      WebSocket manager                        (Phase 10)
    app/core/          config, security, locks, telemetry, redis
    alembic/versions/  migrations, including the contract-overlap EXCLUDE
    tests/             real Postgres; constraints are never mocked

## Local loop

    docker compose -f ../../docker-compose.yml up -d postgres redis
    uv sync --extra dev
    uv run alembic upgrade head
    uv run python -m app.seed
    uv run pytest -v
    uv run uvicorn app.main:app --reload

The suite needs a real Postgres and a real Redis. The contract non-overlap
EXCLUDE constraint and the advisory lock are properties of the database, so
neither can run against SQLite and neither can be proven by a mock.

## Adding an entity

1. Model in `app/models/`, with `AuditedEntity` unless it is a pure line-item
   child of another entity.
2. Repository in `app/repositories/hr.py` — model plus a unique `public_id`
   prefix. Add it to `ALL_REPOSITORIES` so the collision test covers it.
3. Service extending `BaseService`. If the table carries an `EXCLUDE`
   constraint, a deferred constraint or a partial unique index, translate the
   SQLSTATE in **every** mutation method that can move a row into the
   constrained set — see `BaseService`'s docstring.
4. Router, naming the role set from `app/models/enums.py` that matches
   Architecture 5 for that module.
5. Migration. Never `alembic revision --autogenerate` without reading the
   result; raw SQL is expected for exclusion constraints.
