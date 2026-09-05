"""Generic registry mapping a syncable entity_type -> everything app/services/sync.py
needs to pull/push it, without app/services/sync.py or app/api/v1/routers/sync.py
ever importing a domain model by name. An entity becomes syncable the moment
something calls register_syncable_entity for it — nothing in the sync service or
router changes.

That indirection is what let the entire domain be replaced in Phase 0 without
touching the engine: the deleted entities' registrations went with them, and
Phase 8's `attendance` / `time_off_request` registrations are the only code the
new domain needs to add (Architecture §8.3).

Only entities already built on BaseRepository/BaseService are eligible (they're
the ones with AuditedEntity's version/created_at/updated_at/deleted_at, which
the generic cursor and conflict logic depend on).
"""
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, FrozenSet, List, Optional, Type
from pydantic import BaseModel
from app.core.database import Base
from app.repositories.base import BaseRepository
from app.services.base import BaseService

ALL_OPS: FrozenSet[str] = frozenset({"CREATE", "UPDATE", "DELETE"})


@dataclass(frozen=True)
class SyncableEntity:
    entity_type: str
    model: Type[Base]
    service_factory: Callable[[Any], BaseService]  # (session) -> BaseService instance
    create_schema: Type[BaseModel]
    update_schema: Type[BaseModel]
    serialize: Callable[[Any], Dict[str, Any]]  # model instance -> JSON-safe dict
    create_action: str  # audit action label, e.g. "SYNC_CREATE_NOTE"
    update_action: str
    delete_action: str

    # --- Phase 8 additions -------------------------------------------------
    # Three optional fields, each defaulting to the behaviour the engine had
    # before them, so no existing registration changes meaning. They exist
    # because Architecture §5 states rules the original registry could not
    # express, and an entity that cannot express its own rules must not be
    # registered at all — the engine would become a second, looser path to
    # the database, which §9 forbids outright.

    #: Which operations this entity accepts over sync. Architecture §8.3 scopes
    #: offline capability per entity AND per operation: attendance syncs
    #: check-in/check-out CREATION but never corrections, and a time-off
    #: request syncs its creation but never its approval. Without this, the
    #: generic UPDATE path would let an offline client rewrite an attendance
    #: row that `AttendanceService.correct` guards with `require_hr` — the
    #: correction gate bypassed by choosing a different verb.
    allowed_ops: FrozenSet[str] = ALL_OPS

    #: Applies a CREATE through the entity's OWN service method, with the
    #: authenticated principal. The generic path builds `model(**payload)` and
    #: calls `BaseService.create`, which skips every domain rule the real
    #: method enforces: `employee_for`'s "only your own" check, worked-hours
    #: and status derivation, leave-duration calculation, type policy. A
    #: registration that needs any of those MUST set this.
    #: (session, CurrentUser, payload dict) -> created model instance
    create_handler: Optional[Callable[..., Awaitable[Any]]] = None

    #: Extra WHERE criteria restricting which rows this principal may PULL.
    #: `SyncService.pull` filters by tenant and cursor only; for an entity
    #: holding per-employee rows that is a data leak, not a default — every
    #: authenticated login would receive the whole organisation's attendance.
    #: Return `[false()]` to deny everything.
    #: (session, CurrentUser) -> list of SQLAlchemy criteria
    scope_filter: Optional[Callable[..., Awaitable[List[Any]]]] = None


_REGISTRY: Dict[str, SyncableEntity] = {}


def register_syncable_entity(entry: SyncableEntity) -> None:
    _REGISTRY[entry.entity_type] = entry


def get_syncable_entity(entity_type: str) -> Optional[SyncableEntity]:
    return _REGISTRY.get(entity_type)


def registered_entity_types() -> list[str]:
    return list(_REGISTRY.keys())
