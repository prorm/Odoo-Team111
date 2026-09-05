# Harmonix360 Offline Sync — Implementation Summary

Companion to `HARMONIX360_ARCHITECTURE.md` §13.3 (the canonical architecture log).
This file is the practical "what shipped, where, and how to prove it" reference for the offline synchronization layer.

## What was built

A generic, entity-agnostic offline layer: any entity registered on
`BaseRepository`/`BaseService` gets offline read + queued-write +
sync-on-reconnect + real conflict detection, the same way audit logging and
hashid namespacing are standard. Two entities are registered as
proof of genericity — `note` and `asset` — with zero entity-specific code in
the sync engine itself.

## Backend

| File | What it does |
|---|---|
| `app/core/clock.py` | `server_utc_now()` — Postgres-engine time, session-timezone-immune. |
| `app/core/exceptions.py` | `ConflictError` + FastAPI handler → the one 409 payload shape used everywhere. |
| `app/models/mixins.py` | `AuditedEntity` gains real `version` (`version_id_col`) and server-side `updated_at`/`created_at`. |
| `app/repositories/base.py` | Pre-flush version check (no rollback needed on the common path) → `ConflictError`; `StaleDataError` as defense in depth; `create()`'s public_id fixup no longer double-bumps version. |
| `app/models/entities.py` | `SyncMutation` (idempotency log). |
| `alembic/versions/013_add_optimistic_version.py` | Adds `version` to the 5 `AuditedEntity` tables. |
| `alembic/versions/014_sync_mutations_table.py` | Creates `sync_mutations` + grants. |
| `app/services/sync_registry.py` | Generic entity registry (mirrors `resource_registry.py`). |
| `app/services/sync_entities.py` | Registers `note`, `asset`. |
| `app/services/sync.py` | `SyncService.pull()` / `.push()` — cursor, delta queries, per-op savepoints, conflict pre-check, idempotent replay. |
| `app/schemas/sync.py`, `app/schemas/note.py` | Pydantic request/response shapes. |
| `app/api/v1/routers/sync.py` | `GET /sync/pull`, `POST /sync/push`. |
| `app/api/v1/routers/notes.py` | Plain REST CRUD for Notes — the "any other online client" path, not something the offline SPA itself calls. |

## Frontend

| File | What it does |
|---|---|
| `src/lib/offline-db.ts` | IndexedDB (`idb`): `cache`, `outbox`, `conflicts`, `meta` stores. |
| `src/lib/reachability.ts` | Dual online check: `navigator.onLine` AND a live `/health` ping. |
| `src/lib/sync-engine.ts` | Push-then-pull on reconnect; resolves same-batch chained local-id dependencies; conflict resolution (`Keep Mine` / `Overwrite`). |
| `src/hooks/useOfflineMutation.ts` | Generic `useOfflineEntities` (read) / `useOfflineMutation` (create/update/remove) — entity-agnostic. |
| `src/components/OfflineBanner.tsx`, `ConflictModal.tsx` | UI components. |
| `src/routes/notes/NotesPage.tsx` | Reference Surface exercising the whole stack. |

## Scope boundary

Single-server outbox sync — one Postgres, one backend, client-held cursor and
outbox. Not distributed replication, not CRDTs. Conflicts are surfaced, never
silently resolved. See `HARMONIX360_ARCHITECTURE.md` §13.3 for what was deliberately left out and why.

## How to run it locally

```bash
# 1. Postgres + Redis
docker compose up -d postgres redis   # from repo root

# 2. Migrate a clean dev database
cd harmonix360/backend
DATABASE_URL="postgresql+asyncpg://harmonix360_app:app_password@localhost:5544/harmonix360_dev" \
MIGRATION_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5544/harmonix360_dev" \
.venv/Scripts/python.exe -m alembic upgrade head

# 3. Backend (repo-root .env must point DATABASE_URL/MIGRATION_DATABASE_URL at harmonix360_dev)
uvicorn app.main:app --host 127.0.0.1 --port 8000

# 4. Frontend
cd ../frontend
npm run dev   # http://localhost:3000/notes, proxies /api and /health to :8000
```

## Verification

```bash
pip install playwright && playwright install chromium   # one-time
python verify_offline_sync.py
```

Runs against the stack above and prints raw JSON/DB/IndexedDB output for all four required proofs (offline persistence, push/pull reconciliation, live conflict with a real 409 + rendered modal, idempotent replay verified at both the API and database-row level). Exits non-zero on any assertion failure.

## Demoing it by hand

1. Open `http://localhost:3000/notes`.
2. DevTools → Network → "Offline". Add/edit a note — it saves instantly, the offline banner appears.
3. Turn network back on — outbox drains automatically within ~5s, banner disappears.
4. To see a conflict: create a note, go offline, edit it. In another tab (or via `curl -X PATCH .../api/v1/notes/<id>`) edit the same note while online. Reconnect the offline tab — the conflict modal appears with both versions.
