"""
Live proof that AuditedEntity / BaseRepository / BaseService are reusable
scaffolding, not just refactored Asset-specific code.

Exercises the Note entity (app/models/entities.py:Note, app/repositories/note.py,
app/services/note.py) end-to-end: create, read, list, update, soft-delete —
entirely through the base classes, no copy-pasted repo/service logic. Also
proves the hashid-collision fix: an Asset's public_id is rejected when handed
to NoteRepository.get_by_public_id instead of silently resolving.
"""
import asyncio
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.services.note import NoteService
from app.repositories.asset import AssetRepository
from app.models.entities import AuditLog


async def main():
    async with AsyncSessionLocal() as db:
        notes = NoteService(db)

        print("=" * 80)
        print("1. CREATE — NoteService.create_note (BaseService.create -> BaseRepository.create)")
        print("=" * 80)
        note = await notes.create_note(content="Scaffolding audit: first live note", actor_email="qa@harmonix360.com")
        await db.flush()
        print(f"id={note.id} public_id={note.public_id!r} tenant_id={note.tenant_id} "
              f"created_at={note.created_at} deleted_at={note.deleted_at}")
        assert note.public_id.startswith("note_"), "public_id not namespaced with 'note_' prefix"

        print("\n" + "=" * 80)
        print("2. READ — NoteService.get_or_404 (inherited from BaseService, no override)")
        print("=" * 80)
        fetched = await notes.get_or_404(note.public_id)
        print(f"fetched id={fetched.id} public_id={fetched.public_id!r} content={fetched.content!r}")
        assert fetched.id == note.id

        print("\n" + "=" * 80)
        print("3. LIST — NoteService.list (inherited from BaseService, no override)")
        print("=" * 80)
        rows, total = await notes.list(limit=10, offset=0)
        print(f"total={total} returned={len(rows)}")
        print(f"first row: id={rows[0].id} public_id={rows[0].public_id!r}")

        print("\n" + "=" * 80)
        print("4. UPDATE — NoteService.update_note")
        print("=" * 80)
        updated = await notes.update_note(note.public_id, content="Scaffolding audit: edited", actor_email="qa@harmonix360.com")
        print(f"id={updated.id} content={updated.content!r} updated_at={updated.updated_at}")
        assert updated.content == "Scaffolding audit: edited"

        print("\n" + "=" * 80)
        print("5. DELETE — NoteService.delete_note (soft delete)")
        print("=" * 80)
        deleted = await notes.delete_note(note.public_id, actor_email="qa@harmonix360.com")
        print(f"id={deleted.id} deleted_at={deleted.deleted_at}")
        assert deleted.deleted_at is not None

        after_delete = await notes.repo.get_by_public_id(note.public_id)
        print(f"get_by_public_id after soft-delete -> {after_delete!r} (must be None: deleted rows are excluded)")
        assert after_delete is None

        print("\n" + "=" * 80)
        print("6. AUDIT TRAIL — audit_logs rows written for entity='Note', entity_id=note.public_id")
        print("=" * 80)
        result = await db.execute(
            select(AuditLog)
            .where(AuditLog.entity == "Note", AuditLog.entity_id == note.public_id)
            .order_by(AuditLog.id.asc())
        )
        audit_rows = result.scalars().all()
        for row in audit_rows:
            print(f"id={row.id} action={row.action!r} actor={row.actor!r} "
                  f"before_diff={row.before_diff} after_diff={row.after_diff}")
        assert [r.action for r in audit_rows] == ["CREATE_NOTE", "UPDATE_NOTE", "DELETE_NOTE"]

        print("\n" + "=" * 80)
        print("7. COLLISION-FIX PROOF — an Asset's public_id must NOT resolve through NoteRepository")
        print("=" * 80)
        asset_repo = AssetRepository(db)
        any_asset_result = await db.execute(select(AssetRepository.model).limit(1))
        sample_asset = any_asset_result.scalar_one_or_none()
        if sample_asset:
            print(f"sample asset public_id={sample_asset.public_id!r}")
            cross_lookup = await notes.repo.get_by_public_id(sample_asset.public_id)
            print(f"NoteRepository.get_by_public_id(asset.public_id) -> {cross_lookup!r} (must be None: prefix mismatch)")
            assert cross_lookup is None
        else:
            print("no assets in DB to test cross-entity lookup against — skipped")

        await db.commit()
        print("\n" + "=" * 80)
        print("DONE — committed.")
        print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
