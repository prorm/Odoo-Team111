"""Generic registry mapping a syncable entity_type -> everything app/services/sync.py
needs to pull/push it, without app/services/sync.py or app/api/v1/routers/sync.py
ever importing Asset/Note/etc. by name. Structurally identical to
app/services/resource_registry.py's resolver-dict pattern: a new entity becomes
syncable the moment something calls register_syncable_entity for it — nothing
in the sync service or router changes.

Only entities already built on BaseRepository/BaseService are eligible (they're
the ones with AuditedEntity's version/created_at/updated_at/deleted_at, which
the generic cursor and conflict logic depend on).
"""
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Type
from pydantic import BaseModel
from app.core.database import Base
from app.repositories.base import BaseRepository
from app.services.base import BaseService


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


_REGISTRY: Dict[str, SyncableEntity] = {}


def register_syncable_entity(entry: SyncableEntity) -> None:
    _REGISTRY[entry.entity_type] = entry


def get_syncable_entity(entity_type: str) -> Optional[SyncableEntity]:
    return _REGISTRY.get(entity_type)


def registered_entity_types() -> list[str]:
    return list(_REGISTRY.keys())
