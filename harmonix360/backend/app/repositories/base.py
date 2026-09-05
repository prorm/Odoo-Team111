from datetime import datetime, timezone
from typing import Any, Dict, Generic, TypeVar, Optional, List, Tuple, Type
from sqlalchemy import select, func, inspect, update as sa_update
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import Base
from app.core.security import encode_public_id, decode_public_id
from app.core.exceptions import ConflictError

ModelT = TypeVar("ModelT", bound=Base)


def dump_entity_columns(entity: Any) -> Dict[str, Any]:
    """Entity-agnostic column dump for a ConflictError's `current_state` — every
    AuditedEntity subclass has different columns, so BaseRepository can't know a
    Pydantic response shape (that lives with the entity-specific router/service).
    Values are stringified where JSON can't represent them natively (datetimes,
    Decimals, enums) so this drops straight into a JSONResponse body."""
    out: Dict[str, Any] = {}
    for col in inspect(entity).mapper.columns:
        value = getattr(entity, col.name)
        if value is None or isinstance(value, (str, int, float, bool)):
            out[col.name] = value
        else:
            out[col.name] = str(value)
    return out


class BaseRepository(Generic[ModelT]):
    """Generic CRUD for any model built on the AuditedEntity mixin.

    Subclasses set two class attributes:
        model:             the SQLAlchemy model class
        public_id_prefix:  the entity's namespace (e.g. "ast"), used to encode/
                            decode public_id so cross-entity ids can't collide.

    get_by_public_id always decodes-then-queries-by-id (the AssetRepository
    strategy) rather than comparing the public_id column directly, so it stays
    correct even if public_id ever stops being indexed/unique.
    """

    model: Type[ModelT]
    public_id_prefix: str

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, internal_id: int) -> Optional[ModelT]:
        stmt = select(self.model).where(self.model.id == internal_id, self.model.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id_fresh(self, internal_id: int) -> Optional[ModelT]:
        """Like `get_by_id`, but bypasses the identity map via `populate_existing`.

        A plain `select()` returns the SAME Python object already tracked in
        this session if one with this primary key is loaded, with whatever
        locally-mutated (possibly dirty, uncommitted) attribute values it
        currently holds — it does not overwrite them from the query's result
        by default. That is exactly wrong for conflict recovery: the caller
        holds a stale/dirty copy of this exact row and needs the row another
        writer actually committed, not its own pending edits echoed back.
        """
        stmt = (
            select(self.model)
            .where(self.model.id == internal_id, self.model.deleted_at.is_(None))
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_public_id(self, public_id: str) -> Optional[ModelT]:
        internal_id = decode_public_id(public_id, self.public_id_prefix)
        if internal_id is None:
            return None
        return await self.get_by_id(internal_id)

    async def list_active(
        self, limit: int = 50, offset: int = 0, tenant_id: str = "default"
    ) -> Tuple[List[ModelT], int]:
        count_stmt = select(func.count(self.model.id)).where(
            self.model.tenant_id == tenant_id, self.model.deleted_at.is_(None)
        )
        total_count = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(self.model)
            .where(self.model.tenant_id == tenant_id, self.model.deleted_at.is_(None))
            .order_by(self.model.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return list(rows), total_count

    async def create(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        await self.session.flush()
        if not entity.public_id or entity.public_id == "temp":
            new_public_id = encode_public_id(entity.id, self.public_id_prefix)
            # Deliberately a Core-level UPDATE, not `entity.public_id = ...`
            # followed by a normal flush: with version_id_col now live on
            # AuditedEntity, an ORM-tracked UPDATE to fix up the placeholder
            # "temp" public_id right after INSERT would itself count as a
            # version-bumping write, so every newly created row would start
            # at version 2 instead of 1 the moment anything else in the same
            # request (e.g. the audit log insert a few lines up the call
            # stack) triggers the next flush. A Core UPDATE bypasses the
            # unit-of-work's version check entirely — correct here because
            # this is a same-transaction, same-row, no-concurrent-writer-
            # possible fixup of a row nothing else can see yet, not a
            # optimistically-guarded write. `set_committed_value` then tells
            # the ORM this attribute is already in sync with the DB, so no
            # later flush re-issues it as a second (version-bumping) UPDATE.
            await self.session.execute(
                sa_update(self.model).where(self.model.id == entity.id).values(public_id=new_public_id),
                execution_options={"synchronize_session": False},
            )
            set_committed_value(entity, "public_id", new_public_id)
        return entity

    async def update(self, entity: ModelT) -> ModelT:
        """Flushes whatever attributes the caller already mutated on `entity`.

        Real optimistic concurrency (HARMONIX360_ARCHITECTURE.md Section 2 rule 10):
        `AuditedEntity.version` is a SQLAlchemy `version_id_col`, so the flush's
        UPDATE carries `WHERE id=:id AND version=:loaded_version` and bumps
        `version` — generated by the ORM, not hand-rolled here.

        A conflict is detected with a cheap pre-flush read-and-compare rather
        than by waiting for the flush to fail: a failed flush leaves the
        Session needing a rollback before it can be used for anything else
        (even reading the row that actually won), which is fine for a plain
        request — `get_db()` rolls back on any exception — but wrong for a
        sync-push batch, where nine sibling mutations still need this same
        session afterward. The pre-check keeps the common case entirely
        rollback-free. `StaleDataError` is still caught below as defense in
        depth for the sub-millisecond window between the pre-check and the
        flush; that path can't safely re-query (the session needs its own
        transaction/savepoint rolled back first, by whichever caller owns
        that scope), so its `ConflictError` carries a best-effort payload
        instead of a freshly re-read one.
        """
        current_version = await self._read_current_version(entity.id)
        if current_version != entity.version:
            current = await self.get_by_id_fresh(entity.id)
            raise await self._conflict_from_fresh_read(entity, current)
        try:
            await self.session.flush()
        except StaleDataError as exc:
            raise self._conflict_fallback(entity) from exc
        return entity

    async def soft_delete(self, entity: ModelT) -> ModelT:
        current_version = await self._read_current_version(entity.id)
        if current_version != entity.version:
            current = await self.get_by_id_fresh(entity.id)
            raise await self._conflict_from_fresh_read(entity, current)
        entity.deleted_at = datetime.now(timezone.utc)
        try:
            await self.session.flush()
        except StaleDataError as exc:
            raise self._conflict_fallback(entity) from exc
        return entity

    async def _read_current_version(self, internal_id: int) -> Optional[int]:
        stmt = select(self.model.version).where(self.model.id == internal_id, self.model.deleted_at.is_(None))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _conflict_from_fresh_read(self, stale_entity: ModelT, current: Optional[ModelT]) -> ConflictError:
        entity_type = type(stale_entity).__name__
        if current is None:
            # Lost the race to a hard-gone row (deleted between our read and
            # our pre-check) rather than another update.
            return ConflictError(
                entity_type=entity_type,
                entity_id=stale_entity.public_id,
                current_version=stale_entity.version + 1,
                current_state={"deleted_at": "unknown — entity no longer resolvable"},
            )
        return ConflictError(
            entity_type=entity_type,
            entity_id=current.public_id,
            current_version=current.version,
            current_state=dump_entity_columns(current),
        )

    def _conflict_fallback(self, stale_entity: ModelT) -> ConflictError:
        return ConflictError(
            entity_type=type(stale_entity).__name__,
            entity_id=stale_entity.public_id,
            current_version=stale_entity.version + 1,
            current_state={"note": "concurrent write detected at the last instant; re-pull for exact current state"},
            message="Entity was modified by another writer at the exact moment of this write.",
        )
