from typing import Generic, TypeVar, Optional, List, Tuple, Any, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status
from app.core.database import Base
from app.repositories.base import BaseRepository
from app.audit.logger import AuditLogger

ModelT = TypeVar("ModelT", bound=Base)


class BaseService(Generic[ModelT]):
    """Wires a repo to an entity-name label so 404s and audit-log `entity=`
    values are generated once instead of hand-typed in every service method.

    CONTRACT — database-constraint translation (Architecture §6)
    ------------------------------------------------------------
    create()/update()/soft_delete() below flush to Postgres, so any table-level
    constraint the entity carries fires *here*, as a raw `IntegrityError`, not
    at the call site that set the attribute. This class deliberately does NOT
    catch it: what an exclusion or deferred-constraint violation *means* is
    domain knowledge (for a contract it is "this employee already has an active
    contract covering these dates" -> 409; for a background job it may be
    "retry" or "leave in place"), and guessing one translation for every entity
    would be worse than none.

    The obligation therefore sits on the subclass. Any service method that
    mutates a table carrying an `EXCLUDE` constraint, a `DEFERRABLE` constraint,
    or a partial unique index MUST wrap its create()/update() call and translate
    the resulting SQLSTATE into a domain outcome — and must do so in EVERY
    mutation method that can move a row into the constrained set, not just the
    obvious one.

    The trap this contract exists to close, stated in PeoplePay360's own terms:
    `contracts_active_period_overlap_excl` is predicated on
    `WHERE (status = 'active')`, so a status-only UPDATE that sets a draft or
    cancelled contract to active is *exactly* what moves a row into the
    constrained set — and it looks completely harmless at the call site.
    Remembering the guard on the INSERT path while skipping it on the UPDATE
    path is the specific inconsistency this note exists to prevent; there is a
    test pinning that exact case in
    tests/test_contract_overlap_constraint.py.

        EXCLUSION_VIOLATION_SQLSTATE = "23P01"

        try:
            return await self.create(contract, ...)
        except IntegrityError as exc:
            await self.session.rollback()
            if getattr(exc.orig, "sqlstate", None) == EXCLUSION_VIOLATION_SQLSTATE:
                raise HTTPException(status_code=409, detail=...) from exc
            raise
    """

    def __init__(self, session: AsyncSession, repo: BaseRepository[ModelT], entity_name: str):
        self.session = session
        self.repo = repo
        self.entity_name = entity_name

    async def get_or_404(self, public_id: str) -> ModelT:
        entity = await self.repo.get_by_public_id(public_id)
        if not entity:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{self.entity_name} not found")
        return entity

    async def list(self, limit: int = 50, offset: int = 0, tenant_id: str = "default") -> Tuple[List[ModelT], int]:
        return await self.repo.list_active(limit, offset, tenant_id)

    async def audit(
        self,
        actor: str,
        action: str,
        entity_id: str,
        before_diff: Optional[Dict[str, Any]] = None,
        after_diff: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
    ):
        return await AuditLogger.log_mutation(
            session=self.session,
            actor=actor,
            action=action,
            entity=self.entity_name,
            entity_id=entity_id,
            before_diff=before_diff,
            after_diff=after_diff,
            reason=reason,
        )

    async def create(
        self, entity: ModelT, *, actor: str, action: str, after_diff: Optional[Dict[str, Any]] = None
    ) -> ModelT:
        created = await self.repo.create(entity)
        await self.audit(actor, action, created.public_id, after_diff=after_diff)
        return created

    async def update(
        self,
        entity: ModelT,
        *,
        actor: str,
        action: str,
        before_diff: Optional[Dict[str, Any]] = None,
        after_diff: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
    ) -> ModelT:
        updated = await self.repo.update(entity)
        await self.audit(actor, action, updated.public_id, before_diff=before_diff, after_diff=after_diff, reason=reason)
        return updated

    async def soft_delete(self, entity: ModelT, *, actor: str, action: str, reason: Optional[str] = None) -> ModelT:
        deleted = await self.repo.soft_delete(entity)
        await self.audit(actor, action, deleted.public_id, reason=reason)
        return deleted
