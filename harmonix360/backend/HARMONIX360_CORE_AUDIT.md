# Harmonix360 Core/Domain Audit

Scope: `app/` (ai, api/v1, audit, core, jobs/tasks, mcp, middleware, models, repositories, schemas, services, workflows). Excludes `.venv`, `__pycache__`.

Legend: **CORE** = works unchanged for any product surface (PS). **DOMAIN** = AssetFlow-specific, must be replaced per PS. **HYBRID** = generic shape, hardcoded implementation.

---

## 1. Repository / Service / Router pattern

**Classification: DOMAIN (no base class exists) — the pattern is copy-paste, not extend-a-base-class.**

Evidence:
- `app/repositories/asset.py:7-51` (`AssetRepository`) and `app/repositories/transfer.py:11-59` (`TransferRepository`) are near-identical: `get_by_id`, `get_by_public_id`, `list_*`, `create`, `update`, each reimplementing the same `select(...).where(Model.deleted_at.is_(None))` / flush-and-assign-`public_id` logic against a hardcoded model class. There is no `BaseRepository[T]` anywhere in `app/repositories/`.
- `app/core/database.py:23-24` defines `class Base(DeclarativeBase): pass` — no mixin. Every entity in `app/models/entities.py` (e.g. `Asset` lines 77-108, `TransferRequest` lines 134-154, `ResourceBooking` lines 156-173) hand-repeats the same `id`, `public_id`, `tenant_id`, `created_at`, `updated_at`, `deleted_at`, `version` columns rather than inheriting them from a shared `AuditedEntity`/`TenantEntity` mixin.
- Inconsistency between the two "identical" repos: `AssetRepository.get_by_public_id` (asset.py:16-20) **decodes** the hashid to an internal integer id and queries by `id`. `TransferRepository.get_by_public_id` (transfer.py:23-29) instead queries directly on the stored `public_id` string column. Same conceptual operation, two different implementations — proof there's no shared contract being followed, just convention-by-imitation ("follows the same pattern as AssetRepository", transfer.py:2).
- `app/services/asset.py` (`AssetService`, 104 lines) and `app/services/transfer.py` (`TransferService`, 204 lines) each hand-wire: repo instantiation in `__init__`, DTO→model mapping, `AuditLogger.log_mutation` calls with a hardcoded `entity="Asset"` / `entity="TransferRequest"` string (asset.py:47, transfer.py:56/110/182), and HTTPException 404 boilerplate (asset.py:56-58, transfer.py:76-80/143-148/198-203).
- `app/repositories/booking.py:12-29` (`BookingRepository`) is explicitly labeled "Minimal ... for MCP create_booking tool" and again reimplements `get_by_public_id`/`create` from scratch — a third copy of the same 15 lines.

What would need to change: introduce a generic `BaseRepository[ModelT]` parameterized on the SQLAlchemy model (get_by_id/get_by_public_id/list/create/update using a single hashid-decode-or-lookup strategy chosen once) and a `BaseService` that takes an entity-name string and repo instance so audit-log `entity=` labels and 404 messages aren't hand-typed per service. A shared `AuditedEntity` declarative mixin (id/public_id/tenant_id/timestamps/soft-delete/version) would remove the copy-pasted columns from every model. Currently, adding a new entity (e.g. `Venue` for a booking PS) requires copying `asset.py`'s repository+service+router files and renaming, not extending anything.

---

## 2. Audit logging (`app/audit`, `app/jobs/audit_middleware.py`)

**Classification: CORE — genuinely entity-agnostic.**

Evidence:
- `app/audit/logger.py:6-35` — `AuditLogger.log_mutation(session, actor, action, entity, entity_id, before_diff, after_diff, reason, request_id)` takes `entity`/`entity_id` as plain strings; nothing in the function references Asset/TransferRequest types. It writes to the generic `AuditLog` model (`app/models/entities.py:275-292`), whose columns (`actor`, `action`, `entity`, `entity_id`, `before_diff`, `after_diff`, `reason`) are all untyped strings/JSON — no FK to any domain table.
- `app/jobs/audit_middleware.py:20-69` (`JobAuditMiddleware`, a `TaskiqMiddleware`) wraps **every** Taskiq task generically via `message.task_name`/`message.task_id` (lines 44-54) — it has no knowledge of what task it's wrapping. Registered once in `app/jobs/broker.py:30` (`broker.add_middlewares([JobAuditMiddleware()])`), so any future task (e.g. `evaluate_booking_decision`) is audit-logged automatically with zero extra code.
- The immutability enforcement is DB-level and generic: `alembic/versions/001_initial_schema.py:329-330` (`REVOKE UPDATE, DELETE ON audit_logs FROM harmonix360_app;` / `... FROM PUBLIC;`), re-asserted in `002_week2_transfer_ai.py:36-37`.
- Callers do hardcode the `entity=` string per call site (e.g. `app/services/asset.py:47` `entity="Asset"`, `app/mcp/server.py:132` `entity="Asset"`), but that's the caller supplying a parameter to a generic function — not a genericity defect in the logger itself.

What would need to change: nothing in the audit subsystem itself. (The only adjacent HYBRID note: `app/models/entities.py` also has a near-duplicate `AuditEvent` table (lines 294-312) with a richer schema — `actor_type`, `correlation_id`, `trace_id` — that appears unused by any code path found in this audit; two parallel audit-log shapes exist but only `AuditLog`/`AuditLogger` is wired up.)

---

## 3. AI Decision Node (`app/ai/decision_nodes.py`, `app/workflows`)

**Classification: HYBRID — the class signature is generic, but the actual behavior is hardcoded to transfer/asset review.**

Evidence:
- `AIDecisionNode.evaluate(workflow_context: dict, task_type: str = "transfer_decision")` (`app/ai/decision_nodes.py:55-56`) takes a generic `dict` and a `task_type` string — no `TransferRequest` or `Asset` import/type-hint anywhere in the file (confirmed by reading all 118 lines; only import is `from app.ai.provider_router import generate, AIUnavailableError` at line 15). In that narrow sense the function signature is entity-agnostic.
- However, the prompt is **not actually parameterized by `task_type`** — it always builds `prompt = TRANSFER_DECISION_PROMPT.format(context=context_str)` (line 65), ignoring the `task_type` argument entirely. `TRANSFER_DECISION_PROMPT` (lines 30-43) is hardcoded text: `"You are an enterprise asset management AI reviewing a transfer request... APPROVE if the transfer reason is clear, the asset is in good condition... ESCALATE if... the asset is high-value (purchase cost > $5000)..."`. Calling `evaluate()` with `task_type="booking_decision"` today would silently still use the transfer-approval prompt — the `task_type` parameter is dead for prompt selection (it's only forwarded to `generate()` for caching purposes, see `provider_router.py:175` and `cache.py:18-22`).
- The output contract is also transfer-shaped: `AIDecision.decision: Literal["approve", "escalate", "reject"]` (line 22) is a fixed 3-way enum baked into the Pydantic model, not passed in as a status-mapping.
- Downstream, `app/services/transfer.py:67-128` (`process_ai_decision`) hardcodes `TransferStatus.PENDING_REVIEW` (line 89) as the universal landing state for any AI proposal, and `app/jobs/tasks/transfer_decision.py:16-56` (`evaluate_transfer_decision`) directly imports `TransferService` (line 11) and is the only caller of `AIDecisionNode.evaluate()` — there is no generic "AI reviews entity X, human overrides" job; there's exactly one instantiation, for transfers.
- `app/workflows/engine.py` (`WorkflowEngine.validate_transition`, lines 36-57) itself is CORE-clean: it operates purely on `StateMachineDefinition`/`Transition` dataclasses (lines 5-33) keyed by string states/actions, with one exception — `user_role: UserRole` (engine.py:41, and `Transition.roles: List[UserRole]` at line 10) is typed to the AssetFlow `UserRole` enum (`app/models/enums.py:3-7`: EMPLOYEE/ASSET_MANAGER/DEPARTMENT_HEAD/ADMIN) rather than a generic role type — so even the state machine engine leaks one AssetFlow-specific type.

What would need to change:
1. `AIDecisionNode.evaluate()` should accept a `prompt_template: str` (or a `task_type → prompt` registry) instead of hardcoding `TRANSFER_DECISION_PROMPT` at line 65, so a booking-approval or fraud-triage caller can supply its own prompt while reusing the parse/fallback/audit-shape logic.
2. `AIDecision.decision` (line 22) should take its allowed literal values from a caller-supplied status-mapping rather than a fixed `Literal["approve","escalate","reject"]`, so a "flag/clear/investigate" fraud triage node doesn't have to force its vocabulary into transfer terms.
3. `TransferService.process_ai_decision` (transfer.py:67) and `evaluate_transfer_decision` (transfer_decision.py:16) should be factored into a generic `AIReviewJob(entity_repo, entity_type, status_map)` so a second PS doesn't need to hand-write a parallel `evaluate_booking_decision` task + `BookingService.process_ai_decision` method.
4. `WorkflowEngine`/`Transition` (`engine.py:1-57`) should type roles as `str` or a generic `Role` protocol instead of importing the concrete `UserRole` enum, so a travel-booking PS's own role set doesn't require modifying core workflow code.

This is the single highest-value genericization target, exactly as the task brief anticipated — the audit-log/override machinery around it (Section on process_ai_decision / human_override, transfer.py:130-194) is already nicely factored into "AI proposes, separate audit row for human override," it's just wired one entity deep.

---

## 4. MCP server/tools (`app/mcp/server.py`)

**Classification: DOMAIN (tool implementations) wrapping a CORE-ish transport/auth skeleton.**

Evidence — actual tool names (confirmed by reading, matches the header doc at lines 11-19):
- `create_booking` (`app/mcp/server.py:57-102`) — calls `BookingService.create_booking`, hardcoded to `asset_public_id` parameter (line 59) and `AssetRepository`-backed resolution inside the service.
- `check_asset_status` (lines 105-149) — hardcoded to `AssetService.get_asset` and returns asset-shaped fields (`asset_tag`, `condition`, `is_bookable`, `department_id`, lines 139-146).
- `approve_transfer` (lines 152-193) — hardcoded to `TransferService.human_override` with `override_decision="approve"` fixed (line 179).
- `query_audit_trail` (lines 196-246) — this one is already generic: takes `entity_public_id: str` and delegates to `AuditQueryRepository.get_by_entity_id` (`app/repositories/audit_query.py:15-26`), which itself only filters `AuditLog.entity_id == entity_public_id` with no entity-type assumption. This tool would work unchanged for any PS.
- Auth pattern is generic and reusable: `_validate_api_key()` (lines 51-54) checks a single shared `MCP_AGENT_API_KEY` (`app/core/config.py:47`) — not tool-specific.
- **No reusable wrapper/decorator exists** for "validate API key + open session + call service + audit-log + commit/rollback": every tool hand-repeats the same `async with AsyncSessionLocal() as session: try: ... except Exception as e: return {"status":"error","error":str(e)}` block (lines 81-102, 122-149, 174-193, 215-246). A new PS's MCP tool (e.g. `check_booking_status`) would have to copy this boilerplate by hand — there's no documented base pattern (e.g. a `@mcp_tool_with_audit` decorator) to extend.
- `check_asset_status` could become `check_entity_status(entity_type, entity_public_id)` in shape, but as written it directly instantiates `AssetService` (line 124) and returns Asset-specific fields (condition/is_bookable) — not swappable without a rewrite.

What would need to change: extract a shared async context manager / decorator (open session → validate key → run → audit-log → commit/rollback → uniform error envelope) that each tool wraps its entity-specific call in, so new tools don't re-implement the try/except/audit/commit boilerplate. `check_asset_status` and `approve_transfer` would need their service calls parameterized by entity type to be truly generic; `create_booking` and `query_audit_trail` are closer to reusable already (audit_trail is fully reusable today).

---

## 5. RBAC / auth (`app/core/security.py`, `app/api/v1/deps.py`, `app/models/enums.py`)

**Classification: HYBRID — the mechanism (JWT + role-gated dependency) is generic; the role vocabulary is hardcoded to asset management.**

Evidence:
- `app/core/security.py:26-38` (`create_access_token`) and `:40-45` (`decode_token`) are pure JWT plumbing — no domain leakage, fully CORE.
- `app/api/v1/deps.py:39-49` (`require_role(*allowed_roles: UserRole) -> Callable`) is a generic higher-order dependency factory — the *mechanism* (variadic allowed-roles → FastAPI dependency, with a blanket ADMIN bypass at line 41) would work for any role enum.
- BUT it's hard-typed to the concrete enum: `app/models/enums.py:3-7` — `UserRole(str, enum.Enum)`: `EMPLOYEE`, `ASSET_MANAGER`, `DEPARTMENT_HEAD`, `ADMIN`. `ASSET_MANAGER` is an asset-management-shaped role name, not a generic "manager"/"approver" role. Every router's `require_role(...)` call site hardcodes these four values (e.g. `app/api/v1/routers/assets.py:18` `require_role(UserRole.ASSET_MANAGER, UserRole.ADMIN)`, `app/api/v1/routers/transfers.py:107` same pair).
- `app/api/v1/deps.py:12-16` (`CurrentUser`) and `:18-21` (demo fallback: unauthenticated requests silently become `admin@harmonix360.com`/ADMIN) is a demo/dev shortcut, not permission-system design — worth flagging separately as a security smell, not a genericity issue.
- `app/api/v1/routers/auth.py:11-20` — login is two hardcoded demo credential pairs (`admin@harmonix360.com`/`admin123`, `employee@harmonix360.com`/`emp123`) with no real user table lookup despite a `User` model existing (`app/models/entities.py:21-38`) — this endpoint doesn't even use `verify_password`/the `users` table. DOMAIN/prototype-only, would need full rebuild for any PS, not just renaming.
- No fine-grained **permissions** system exists at all — only 4 fixed roles with an implicit ADMIN-always-wins rule (`deps.py:41`, `engine.py:52`). There's no resource-action permission model (e.g. `booking:approve`, `asset:transfer`) to genericize; the hardcoding is at the "what roles exist" layer.

What would need to change: `UserRole` (`enums.py:3-7`) needs to become either a per-tenant/per-PS configurable role set (DB-backed or config-driven) rather than a Python enum baked into the codebase, and `require_role`/`WorkflowEngine.Transition.roles` should accept generic role identifiers (strings or a `Role` protocol) instead of importing the concrete enum. `auth.py`'s login endpoint needs a real implementation against the `users` table regardless of PS.

---

## 6. ResourceBooking EXCLUDE constraint (`app/models/entities.py`, `alembic/versions/001_initial_schema.py`)

**Classification: HYBRID — the DB primitive (EXCLUDE + btree_gist + tstzrange) is a strong, fully generic no-double-booking mechanism, but it is currently wired to a single hardcoded column (`asset_id`), not a generic resource reference.**

Evidence:
- `alembic/versions/001_initial_schema.py:19` enables the extension: `CREATE EXTENSION IF NOT EXISTS "btree_gist";` — generic, PS-agnostic infra.
- The constraint itself, lines 167-172:
  ```sql
  ALTER TABLE resource_bookings ADD CONSTRAINT resource_bookings_range_overlap_excl EXCLUDE USING gist (
    asset_id WITH =,
    tstzrange(start_time, end_time) WITH &&
  );
  ```
  This is DB-enforced no-double-booking for a given `asset_id` over an overlapping `tstzrange(start_time, end_time)` — genuinely the strongest reusable primitive found in the codebase (would work identically for booking a venue, a hotel room, a vehicle — any bookable resource) **if** `asset_id` were a generic `resource_id`/`resource_type` pair.
- The model backing it, `app/models/entities.py:156-173` (`ResourceBooking`), hardcodes `asset_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("assets.id"), ...)` (line 161) — a direct FK to the `assets` table, not a polymorphic `(resource_type, resource_id)` pair. So today only assets can be booked; a travel-booking PS's "book a flight seat" or a venue PS's "book a room" would require either (a) reusing the `assets` table by shoehorning flights/rooms into it, or (b) a schema change to genericize the FK.
- `app/repositories/booking.py` and `app/services/booking.py` (read in full above) both hardcode `asset_public_id`/`AssetRepository` — e.g. `booking.py:35` `asset = await self.asset_repo.get_by_public_id(asset_public_id)` and the not-bookable check `booking.py:42-46` (`if not asset.is_bookable`), which reads an Asset-specific column (`Asset.is_bookable`, `entities.py:90`).

What would need to change: replace `ResourceBooking.asset_id` FK (`entities.py:161`) with a generic `resource_type: str` + `resource_id: BigInteger` pair (or a nullable polymorphic FK), and update the EXCLUDE constraint to `EXCLUDE USING gist (resource_type WITH =, resource_id WITH =, tstzrange(start_time,end_time) WITH &&)` so the same table/constraint can hold bookings for any bookable entity type. `BookingService`/`BookingRepository` would need to resolve "is this resource type bookable" generically instead of checking `Asset.is_bookable`.

---

## 7. AI Provider Router (`app/ai/provider_router.py`)

**Classification: CORE — confirmed clean "call an LLM, fall back on failure" with no AssetFlow leakage.**

Evidence:
- `generate(prompt: str, context: dict, task_type: str) -> AIResponse` (lines 175-304) operates purely on strings/dicts. Read in full (305 lines) — no import of any model from `app.models`, no reference to Asset/TransferRequest/Booking anywhere in the file.
- Fallback chain is config-driven: `_PROVIDERS = [ProviderConfig("groq", ...), ProviderConfig("cerebras", ...)]` (lines 108-113), iterated generically at lines 211-299; adding a third provider (the code even has a stubbed comment for Ollama, lines 111-112/167) requires only appending to `_PROVIDERS` and `_CALL_FUNCTIONS` (line 164-168), per the module's own docstring (lines 9-12).
- Rate limiting (`ProviderRateLimiter.check_and_increment`, lines 53-81) keys on `provider` name + current minute — generic, reused from `app/core/rate_limit.py`'s pattern per its own comment (line 57).
- Caching delegates to `app/ai/cache.py`'s `AIResponseCache`, which is also generic except for one detail: `TASK_TYPE_TTLS` (`cache.py:18-22`) hardcodes `"transfer_decision": 300` and `"asset_analysis": 600` as named task types with a `"general": 600` fallback (line 21) used via `.get(task_type, settings.AI_CACHE_DEFAULT_TTL)` (`cache.py:50`) — this degrades gracefully for unknown task types (falls back to the default TTL), so it's not a hard dependency, just AssetFlow-specific tuning values sitting in an otherwise generic cache. Minor HYBRID note, not a blocker.
- One code-quality note unrelated to genericity: `_call_groq`/`_call_cerebras` (lines 120-139, 142-161) `print()` full prompts/responses to stdout (lines 125-127, 134-136, 147-149, 156-158) — debug leftovers, not a domain-coupling issue.

What would need to change: nothing structural. Optionally move `TASK_TYPE_TTLS` (cache.py:18-22) to be populated by each PS/feature registering its own task types rather than being pre-seeded with AssetFlow's two task names, purely for cleanliness.

---

## 8. Notification / Taskiq job pattern (`app/jobs`, `app/jobs/tasks`)

**Classification: HYBRID — the broker/middleware plumbing is CORE; the only notification job implemented is hardcoded to asset state transitions.**

Evidence:
- `app/jobs/broker.py:15-32` — `ListQueueBroker`/`RedisAsyncResultBackend` setup and `JobAuditMiddleware` registration (line 30) are fully generic (see Section 2).
- `app/jobs/tasks/ai_jobs.py:17-44` (`run_ai_generate`) is a thin, generic wrapper around `provider_router.generate()` — takes `prompt/context/task_type`, returns a structured status dict. CORE.
- `app/jobs/tasks/transfer_decision.py:16-56` (`evaluate_transfer_decision`) is transfer-specific by design — directly imports and instantiates `TransferService` (line 11, line 44) and calls `service.process_ai_decision` (line 45). This is the AI-decision job discussed in Section 3.
- `app/jobs/tasks/notifications.py:18-77` (`send_asset_state_change_notification`) is entirely asset-specific and not parameterized at all:
  - Function name itself is `send_asset_state_change_notification` (line 19), taking `asset_public_id, old_status, new_status, actor_email` (lines 19-24) — not `entity_type`/`entity_public_id`.
  - Recipient selection is hardcoded: `stmt = select(User).where(User.role.in_(["ADMIN", "ASSET_MANAGER"]), ...)` (lines 38-41) — literal role-name strings duplicating (and drifting from, since they're plain strings not `UserRole` enum members) the RBAC roles from Section 5.
  - Message text is hardcoded: `title=f"Asset {asset_public_id} status changed"`, `message=f"Asset {asset_public_id} transitioned from {old_status} to {new_status} by {actor_email}."` (lines 55-56) and `type="ASSET_STATE_CHANGE"` (line 54).
  - It writes to the generic `Notification` model (`entities.py:248-260`), which itself has no domain coupling (`user_id`, `type: str`, `title`, `message`, `metadata_json` — all generic) — so the **table** is CORE, only the **task function** is DOMAIN.
  - Called from exactly one place: `app/services/asset.py:91-101`, inside `AssetService.transition_asset`, via `await send_asset_state_change_notification.kiq(...)` — fire-and-forget with try/except so notification failure doesn't block the transition (lines 90-101). This pattern (enqueue-and-don't-block) is itself a good generic pattern, just not extracted.

What would need to change: `send_asset_state_change_notification` (notifications.py:18) should become a generic `send_entity_state_change_notification(entity_type, entity_public_id, old_status, new_status, actor_email, notify_roles)` so any PS's state-transition service can reuse it instead of writing its own near-identical task. The recipient role list (`["ADMIN","ASSET_MANAGER"]`, line 39) should be a parameter, not hardcoded, and should use the `UserRole` enum rather than raw strings to avoid silent drift if role names change.

---

## 9. Hashid public_id system (`app/core/security.py`, usages across repos/models)

**Classification: HYBRID — the encode/decode functions are generic, but there is exactly one shared salt/instance across all entity types, and repositories don't use it consistently.**

Evidence:
- `app/core/security.py:9` — `hashids_instance = Hashids(salt=settings.HASHID_SALT, min_length=8)` is a single module-level instance shared by **every** entity type. `encode_public_id(integer_id: int)` (line 17-18) and `decode_public_id(public_id: str)` (line 20-24) take/return plain ints — no entity-type parameter at all.
- Consequence: because the same salt/instance encodes all tables' integer PKs, `Asset` row `id=5` and `TransferRequest` row `id=5` (and `ResourceBooking` id=5, etc.) all hashid-encode to the **identical** `public_id` string. `AssetRepository.get_by_public_id` (asset.py:16-20) decodes the hashid back to an int and looks it up **only within the `assets` table**, so this doesn't cause a live bug today (each repo scopes its own lookup), but it does mean `public_id`s are not globally unique/self-describing — you cannot tell which table a `public_id` belongs to, and two different entities can share the same public_id string. A generic MCP tool like `query_audit_trail(entity_public_id)` (`app/mcp/server.py:197-246`) or `AuditQueryRepository.get_by_entity_id` (`audit_query.py:15-26`) that queries by `public_id` alone, across entities, is therefore ambiguous without also knowing the `entity` type — and indeed `AuditLog` stores `entity` and `entity_id` as separate columns (`entities.py:286-287`) specifically to disambiguate, confirming the system relies on out-of-band type tagging rather than globally unique ids.
- Usage is inconsistent (also noted in Section 1): `AssetRepository.create`/`get_by_public_id` round-trip through `encode_public_id`/`decode_public_id` (asset.py:16-20, 44-45); `TransferRepository` only encodes on create (`transfer.py:53-54`) and stores/queries the resulting string directly, never calling `decode_public_id` (transfer.py:23-29) — same underlying hashid mechanism, two different retrieval strategies.
- The functions themselves (`encode_public_id`/`decode_public_id`, `security.py:17-24`) are otherwise fully generic — take an int, return a string, and vice versa; no domain types involved.

What would need to change: either (a) namespace the hashid salt/instance per entity type (or prefix the encoded string with a short entity-type tag, similar to how `AuditLogger` mints `f"aud_{uuid4().hex[:12]}"` string ids at `audit/logger.py:20` and `notifications.py:52` mints `f"ntf_{uuid4().hex[:12]}"`) so `public_id`s are unambiguous on their own, or (b) standardize all repositories on one retrieval strategy (decode-then-lookup-by-id, matching `AssetRepository`, rather than the `TransferRepository` string-compare variant) so the "pattern" claimed in `transfer.py`'s own docstring (line 2) is actually true.

---

## Prioritized summary — top items worth genericizing first

1. **AIDecisionNode prompt/status genericization** (`app/ai/decision_nodes.py:56-65`, `AIDecision.decision` at line 22, and the single call site `app/jobs/tasks/transfer_decision.py:16-56`). Highest leverage: "AI reviews an entity, proposes a decision, human can override, both are audit-logged separately" is the single most reusable capability in this codebase (booking approval, fraud triage, expense review — all fit), and the surrounding audit/override machinery (`app/services/transfer.py:67-194`) is already well-designed. The only blocker is the hardcoded `TRANSFER_DECISION_PROMPT` and the fixed `approve/escalate/reject` literal. Smallest change, largest payoff.

2. **Base repository/service layer** (`app/repositories/*.py`, `app/services/*.py`, no base class in `app/core/database.py:23-24`). Every new entity today means copy-pasting ~50-200 lines across 3 files (repo/service/router) and renaming. A `BaseRepository[T]`/`BaseService` plus a shared `AuditedEntity` model mixin would cut that to inheritance + a handful of entity-specific methods, and would also fix the `Asset`-vs-`Transfer` public_id lookup inconsistency found in Sections 1 and 9.

3. **ResourceBooking's EXCLUDE constraint → polymorphic resource reference** (`alembic/versions/001_initial_schema.py:167-172`, `app/models/entities.py:161`). The DB primitive itself (`EXCLUDE USING gist (... WITH =, tstzrange(...) WITH &&)`) is a rare, high-value, already-correct piece of infrastructure — genericizing just the `asset_id` FK to `(resource_type, resource_id)` unlocks "no double-booking" for literally any bookable entity in any PS, without touching the constraint mechanism itself.

4. **MCP tool wrapper boilerplate** (`app/mcp/server.py:81-102, 122-149, 174-193, 215-246`). Every tool hand-repeats open-session/validate-key/audit-log/commit-or-rollback/error-envelope. Extracting that into one decorator or context manager means future PS's MCP tools are additive (new `@mcp.tool()` functions) instead of requiring every author to re-derive the session/audit/error-handling boilerplate correctly.

5. **RBAC role vocabulary** (`app/models/enums.py:3-7` `UserRole`, `app/workflows/engine.py:10,41,52` typed to `UserRole`). Lower urgency than the above (a new PS can plausibly launch by just adding roles to the same enum), but `ASSET_MANAGER` baked into `WorkflowEngine`'s type signature and into every router's `require_role(...)` call means the role set can't be swapped without touching core workflow code — worth decoupling once a second PS is actually being built, not necessarily before.

Not prioritized because already CORE or already acceptably parameterized: audit logging (`app/audit/logger.py`, `app/jobs/audit_middleware.py`), the AI provider fallback router (`app/ai/provider_router.py`), and the `WorkflowEngine` state-machine mechanism itself (`app/workflows/engine.py`, aside from its `UserRole` type leak).
