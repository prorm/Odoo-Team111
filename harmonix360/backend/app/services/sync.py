"""Generic offline-sync engine: GET /sync/pull and POST /sync/push.

Works entirely off app/services/sync_registry.py entries — no entity-specific
code lives here at all, which is why swapping the whole domain out from under
it (AssetFlow's note/asset registrations, deleted in Phase 0) required zero
changes to this file.

Currently dormant: the registry is empty until Phase 8 registers `attendance`
and `time_off_request` (Architecture §8.3). Pull rejects every entity type and
push applies nothing, which is correct behaviour for an engine with no
registered surface — not a fault.

See 02_SYSTEM_ARCHITECTURE.md §8.3 for the cursor/conflict/idempotency design.
"""
import base64
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import server_utc_now
from app.core.telemetry import sync_span
from app.core.exceptions import ConflictError
from app.models.entities import SyncMutation
from app.repositories.base import dump_entity_columns
from app.schemas.sync import (
    PullDeltaEntity, PullResponse, PushErrorDetail, PushMutation,
    PushMutationResult, PushResponse,
)
from app.services.sync_registry import SyncableEntity, get_syncable_entity, registered_entity_types
# Import for its registration side-effect: populates sync_registry with the
# entity types this deployment of Harmonix360 actually syncs.
from app.services import sync_entities  # noqa: F401

logger = logging.getLogger("harmonix360.services.sync")

# See HARMONIX360_ARCHITECTURE.md §13.3: `updated_at` is assigned at flush time,
# not commit time, so two concurrent transactions can commit out of order
# relative to their own timestamps. A pull never hands out a row younger than
# this lag, which is comfortably longer than any single request's transaction
# lifetime here — bounding, not eliminating, the "missed row" race.
SYNC_SAFETY_LAG_SECONDS = 2
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
DEFAULT_PAGE_LIMIT = 200


def _encode_cursor(positions: Dict[str, Tuple[datetime, int]]) -> str:
    raw = {k: [v[0].isoformat(), v[1]] for k, v in positions.items()}
    return base64.urlsafe_b64encode(json.dumps(raw).encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: Optional[str]) -> Dict[str, Tuple[datetime, int]]:
    if not cursor:
        return {}
    raw = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8"))
    return {k: (datetime.fromisoformat(v[0]), v[1]) for k, v in raw.items()}


class SyncService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ---------------------------------------------------------------- pull

    async def pull(
        self, since: Optional[str], entity_types: List[str], tenant_id: str = "default",
        limit: int = DEFAULT_PAGE_LIMIT, user: Any = None,
    ) -> PullResponse:
        """`user` is optional so existing callers keep working, but an entity
        that registers a `scope_filter` and is pulled without one is refused
        rather than silently served unscoped — the failure mode of a
        row-scoped entity leaking every row is worse than a 500."""
        positions = _decode_cursor(since)
        entities: List[PullDeltaEntity] = []
        has_more = False

        for entity_type in entity_types:
            entry = get_syncable_entity(entity_type)
            if entry is None:
                # Unknown entity_type is a client error the router surfaces
                # (400) before calling us — see app/api/v1/routers/sync.py.
                continue

            cursor_ts, cursor_id = positions.get(entity_type, (EPOCH, 0))
            model = entry.model

            # Row-level scoping, from the registration rather than from here:
            # this engine has no idea which column on which model means "whose
            # row this is". Architecture §5's first row ("...but only their
            # own") is a per-entity rule, so the entity supplies it.
            scope: List[Any] = []
            if entry.scope_filter is not None:
                if user is None:
                    raise RuntimeError(
                        f"'{entity_type}' registers a scope_filter but pull() was "
                        "called without a user; refusing to serve it unscoped."
                    )
                scope = await entry.scope_filter(self.session, user)

            stmt = (
                select(model)
                .where(
                    model.tenant_id == tenant_id,
                    *scope,
                    tuple_(model.updated_at, model.id) > tuple_(cursor_ts, cursor_id),
                    model.updated_at <= server_utc_now() - timedelta(seconds=SYNC_SAFETY_LAG_SECONDS),
                )
                .order_by(model.updated_at.asc(), model.id.asc())
                .limit(limit)
            )
            rows = (await self.session.execute(stmt)).scalars().all()

            if len(rows) == limit:
                has_more = True

            for row in rows:
                if row.deleted_at is not None:
                    op = "DELETE"
                    data = None
                elif row.created_at > cursor_ts:
                    op = "CREATE"
                    data = entry.serialize(row)
                else:
                    op = "UPDATE"
                    data = entry.serialize(row)

                entities.append(PullDeltaEntity(
                    entity_type=entity_type,
                    public_id=row.public_id,
                    op=op,
                    version=row.version,
                    updated_at=row.updated_at,
                    data=data,
                ))
                positions[entity_type] = (row.updated_at, row.id)

        return PullResponse(
            cursor=_encode_cursor(positions),
            server_time=datetime.now(timezone.utc),
            has_more=has_more,
            entities=entities,
        )

    # ---------------------------------------------------------------- push

    async def push(self, mutations: List[PushMutation], user: Any) -> PushResponse:
        """Takes the whole principal, not just its email.

        `actor_key` remains the email, so the idempotency key
        (actor_key, client_mutation_id) means exactly what it did before. But a
        registration that routes its CREATE through a domain service needs the
        role and the signed `employee_id` claim as well — "only your own
        attendance" cannot be checked against an email.
        """
        actor_key = user.email
        results: List[PushMutationResult] = []
        # Trace 3 of the three Architecture §8.5 allows: client mutation ->
        # validation -> transaction -> audit. Counts and entity types only; a
        # mutation payload can carry an employee's attendance, and a span is
        # exported to a telemetry backend.
        with sync_span("push_batch", mutation_count=len(mutations), actor_role=getattr(user.role, "value", str(user.role))):
            for mutation in mutations:
                replay = await self._find_replay(actor_key, mutation.client_mutation_id)
                if replay is not None:
                    results.append(replay)
                    continue

                entry = get_syncable_entity(mutation.entity_type)
                if entry is None:
                    result = PushMutationResult(
                        client_mutation_id=mutation.client_mutation_id,
                        outcome="rejected",
                        entity_type=mutation.entity_type,
                        entity_id=mutation.entity_id,
                        error=PushErrorDetail(
                            code="UNKNOWN_ENTITY_TYPE",
                            message=f"'{mutation.entity_type}' is not a syncable entity type. "
                                    f"Registered: {registered_entity_types()}",
                        ),
                    )
                elif mutation.op not in entry.allowed_ops:
                    # Architecture §8.3 scopes offline capability per operation as
                    # well as per entity. An attendance CORRECTION is an UPDATE,
                    # and it is online-only and role-gated; letting it through here
                    # would bypass `require_hr` by choosing a different verb.
                    result = PushMutationResult(
                        client_mutation_id=mutation.client_mutation_id,
                        outcome="rejected",
                        entity_type=mutation.entity_type,
                        entity_id=mutation.entity_id,
                        error=PushErrorDetail(
                            code="OPERATION_NOT_SYNCABLE",
                            message=(
                                f"'{mutation.op}' is not syncable for "
                                f"'{mutation.entity_type}'. Syncable: "
                                f"{sorted(entry.allowed_ops)}. This operation stays "
                                "online-only on purpose."
                            ),
                        ),
                    )
                else:
                    result = await self._apply_one_guarded(entry, mutation, user)

                await self._log_mutation(actor_key, mutation, result)
                results.append(result)

        return PushResponse(results=results)

    async def _find_replay(self, actor_key: str, client_mutation_id: str) -> Optional[PushMutationResult]:
        stmt = select(SyncMutation).where(
            SyncMutation.actor_key == actor_key,
            SyncMutation.client_mutation_id == client_mutation_id,
        )
        row = (await self.session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return PushMutationResult.model_validate(row.result_json)

    async def _log_mutation(self, actor_key: str, mutation: PushMutation, result: PushMutationResult) -> None:
        # `entity_id` is client-submitted and only ever meant to be a hashid
        # public_id (well under 32 chars) — but a buggy or malicious client
        # could send something longer (a client-generated placeholder id that
        # never got resolved server-side, say), and this log write must not
        # be what turns that into an unhandled 500 on an otherwise-correctly-
        # classified rejected/conflict outcome. Truncated defensively; the
        # full value the client sent is still in `result.error` when relevant.
        entity_id = (mutation.entity_id or None)
        if entity_id and len(entity_id) > 32:
            entity_id = entity_id[:32]
        log_row = SyncMutation(
            client_mutation_id=mutation.client_mutation_id,
            actor_key=actor_key,
            entity_type=mutation.entity_type,
            entity_id=entity_id,
            op=mutation.op,
            outcome=result.outcome,
            result_json=json.loads(result.model_dump_json()),
        )
        self.session.add(log_row)
        await self.session.flush()

    async def _apply_one_guarded(
        self, entry: SyncableEntity, mutation: PushMutation, user: Any
    ) -> PushMutationResult:
        """Runs `_apply_one` inside a SAVEPOINT and lets any DB-poisoning
        exception (a failed INSERT/UPDATE) propagate all the way out of the
        `async with` block before this catches it. That order matters: if
        `_apply_one` swallowed the exception internally and returned a normal
        result, `begin_nested()` would try to RELEASE a savepoint Postgres has
        already marked aborted, raising a second, unrelated error. Letting the
        exception cross the `async with` boundary is what makes SQLAlchemy
        issue ROLLBACK TO SAVEPOINT instead, leaving the session usable again
        for the next mutation in this batch.
        """
        try:
            async with self.session.begin_nested():
                with sync_span(
                    "apply_mutation",
                    entity_type=mutation.entity_type,
                    op=mutation.op,
                ):
                    return await self._apply_one(entry, mutation, user)
        except ConflictError as exc:
            # Defense-in-depth: version_id_col's own flush-time check caught a
            # race the pre-check in `_apply_one` didn't (two ops in the same
            # push batch touching the same row, or a genuinely concurrent
            # request racing this one).
            envelope = exc.to_envelope()["error"]
            return PushMutationResult(
                client_mutation_id=mutation.client_mutation_id,
                outcome="conflict",
                entity_type=mutation.entity_type,
                entity_id=exc.entity_id,
                error=PushErrorDetail(**envelope),
            )
        except HTTPException as exc:
            # A domain service refusing this mutation — 403 "not your record",
            # 422 "interval too long", 409 "already checked out". One refused
            # mutation must not abort the rest of the batch, and it must not
            # become a 500 either: it is a per-mutation outcome, which is
            # exactly what `rejected` means. Before Phase 8 no registration
            # called a domain method, so nothing here could raise this.
            detail = exc.detail
            if isinstance(detail, dict):
                detail = detail.get("message", detail)
            return PushMutationResult(
                client_mutation_id=mutation.client_mutation_id,
                outcome="rejected",
                entity_type=mutation.entity_type,
                entity_id=mutation.entity_id,
                error=PushErrorDetail(
                    code="FORBIDDEN" if exc.status_code == 403 else "VALIDATION_ERROR",
                    message=str(detail),
                ),
            )
        except (IntegrityError, ValidationError) as exc:
            return PushMutationResult(
                client_mutation_id=mutation.client_mutation_id,
                outcome="rejected",
                entity_type=mutation.entity_type,
                entity_id=mutation.entity_id,
                error=PushErrorDetail(code="VALIDATION_ERROR", message=str(exc)),
            )

    async def _apply_one(
        self, entry: SyncableEntity, mutation: PushMutation, user: Any
    ) -> PushMutationResult:
        actor_key = user.email
        service = entry.service_factory(self.session)

        if mutation.op == "CREATE":
            return await self._apply_create(entry, service, mutation, user)

        current = await service.repo.get_by_public_id(mutation.entity_id)
        if current is None:
            return PushMutationResult(
                client_mutation_id=mutation.client_mutation_id,
                outcome="rejected",
                entity_type=mutation.entity_type,
                entity_id=mutation.entity_id,
                error=PushErrorDetail(
                    code="NOT_FOUND",
                    message=f"{mutation.entity_type} '{mutation.entity_id}' does not exist "
                            f"or was already deleted",
                ),
            )

        if mutation.known_version != current.version:
            conflict_result = self._conflict_result(mutation, current)
            # Surfaced, not silently resolved (offline-sync requirement):
            # write the audit trail now, since we never reach
            # BaseService.update/soft_delete's own audit call on this path.
            await service.audit(
                actor=actor_key,
                action=f"SYNC_CONFLICT_{mutation.op}",
                entity_id=current.public_id,
                before_diff={"known_version": mutation.known_version, "current_version": current.version},
                reason="client's known_version does not match current server version",
            )
            return conflict_result

        if mutation.op == "UPDATE":
            return await self._apply_update(entry, service, mutation, current, actor_key)
        return await self._apply_delete(entry, service, mutation, current, actor_key)

    def _conflict_result(self, mutation: PushMutation, current: Any) -> PushMutationResult:
        return PushMutationResult(
            client_mutation_id=mutation.client_mutation_id,
            outcome="conflict",
            entity_type=mutation.entity_type,
            entity_id=current.public_id,
            error=PushErrorDetail(
                code="VERSION_CONFLICT",
                message="Entity has been modified since your last known version.",
                details={
                    "entity_type": mutation.entity_type,
                    "entity_id": current.public_id,
                    "known_version": mutation.known_version,
                    "current_version": current.version,
                    "current_state": dump_entity_columns(current),
                },
            ),
        )

    async def _apply_create(
        self, entry: SyncableEntity, service, mutation: PushMutation, user: Any
    ) -> PushMutationResult:
        if entry.create_handler is not None:
            # The entity's OWN service method, with the authenticated
            # principal — same method, same authorization, same derivation and
            # same audit write the REST router reaches (Architecture §9: one
            # path to the database). The generic path below cannot be used for
            # an entity with domain rules: it would build the row straight from
            # the client's payload and skip all of them.
            created = await entry.create_handler(self.session, user, mutation.payload or {})
            return PushMutationResult(
                client_mutation_id=mutation.client_mutation_id,
                outcome="applied",
                entity_type=mutation.entity_type,
                entity_id=created.public_id,
                version=created.version,
            )

        dto = entry.create_schema(**(mutation.payload or {}))
        entity = entry.model(public_id="temp", **dto.model_dump())
        created = await service.create(
            entity, actor=user.email, action=entry.create_action, after_diff=dto.model_dump(mode="json"),
        )
        return PushMutationResult(
            client_mutation_id=mutation.client_mutation_id,
            outcome="applied",
            entity_type=mutation.entity_type,
            entity_id=created.public_id,
            version=created.version,
        )

    async def _apply_update(
        self, entry: SyncableEntity, service, mutation: PushMutation, current: Any, actor_key: str
    ) -> PushMutationResult:
        dto = entry.update_schema(**(mutation.payload or {}))
        changes = dto.model_dump(exclude_unset=True)
        for field, value in changes.items():
            setattr(current, field, value)
        updated = await service.update(
            current, actor=actor_key, action=entry.update_action, after_diff=changes,
        )
        return PushMutationResult(
            client_mutation_id=mutation.client_mutation_id,
            outcome="applied",
            entity_type=mutation.entity_type,
            entity_id=updated.public_id,
            version=updated.version,
        )

    async def _apply_delete(
        self, entry: SyncableEntity, service, mutation: PushMutation, current: Any, actor_key: str
    ) -> PushMutationResult:
        deleted = await service.soft_delete(current, actor=actor_key, action=entry.delete_action)
        return PushMutationResult(
            client_mutation_id=mutation.client_mutation_id,
            outcome="applied",
            entity_type=mutation.entity_type,
            entity_id=deleted.public_id,
            version=deleted.version,
        )
