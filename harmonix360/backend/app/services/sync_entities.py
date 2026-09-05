"""Registers the entity types this Harmonix360 deployment exposes through
/sync/pull and /sync/push. Imported for its registration side effect only
(app/services/sync.py does `from app.services import sync_entities  # noqa: F401`),
mirroring app/services/booking_resources.py's role for resource_registry.

Adding a third synced entity is: one `register_syncable_entity(...)` call here.
Nothing in app/services/sync.py or app/api/v1/routers/sync.py changes.
"""
from app.models.entities import Note, Asset
from app.services.note import NoteService
from app.services.asset import AssetService
from app.schemas.note import NoteCreate, NoteUpdate
from app.schemas.asset import AssetCreate, AssetUpdate
from app.services.sync_registry import SyncableEntity, register_syncable_entity


def _serialize_note(note: Note) -> dict:
    return {
        "id": note.public_id,
        "content": note.content,
        "version": note.version,
        "tenant_id": note.tenant_id,
        "created_at": note.created_at,
        "updated_at": note.updated_at,
    }


def _serialize_asset(asset: Asset) -> dict:
    return {
        "id": asset.public_id,
        "name": asset.name,
        "asset_tag": asset.asset_tag,
        "serial_number": asset.serial_number,
        "category_id": asset.category_id,
        "department_id": asset.department_id,
        "status": asset.status.value if asset.status else None,
        "condition": asset.condition.value if asset.condition else None,
        "location": asset.location,
        "is_bookable": asset.is_bookable,
        "purchase_date": asset.purchase_date,
        "purchase_cost": asset.purchase_cost,
        "version": asset.version,
        "tenant_id": asset.tenant_id,
        "created_at": asset.created_at,
        "updated_at": asset.updated_at,
    }


register_syncable_entity(SyncableEntity(
    entity_type="note",
    model=Note,
    service_factory=lambda session: NoteService(session),
    create_schema=NoteCreate,
    update_schema=NoteUpdate,
    serialize=_serialize_note,
    create_action="SYNC_CREATE_NOTE",
    update_action="SYNC_UPDATE_NOTE",
    delete_action="SYNC_DELETE_NOTE",
))

register_syncable_entity(SyncableEntity(
    entity_type="asset",
    model=Asset,
    service_factory=lambda session: AssetService(session),
    create_schema=AssetCreate,
    update_schema=AssetUpdate,
    serialize=_serialize_asset,
    create_action="SYNC_CREATE_ASSET",
    update_action="SYNC_UPDATE_ASSET",
    delete_action="SYNC_DELETE_ASSET",
))
