"""Contract service.

Phase 0: a thin pass-through. Phase 1 (PS A2) adds active-contract resolution
and — the obligation BaseService's docstring spells out — translation of
SQLSTATE 23P01 from the non-overlap EXCLUDE constraint into a clean 409, on
EVERY mutation path that can move a row into `status = 'active'`, not only on
create.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contract import Contract
from app.repositories.hr import ContractRepository
from app.services.base import BaseService


class ContractService(BaseService[Contract]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, ContractRepository(session), entity_name="Contract")
