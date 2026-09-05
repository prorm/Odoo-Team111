"""Time off services — Type, Allocation and Request.

Phase 0: thin pass-throughs. Phase 2 (PS A4/B4) adds the approve/refuse
transition and the allocation deduction it performs in the same transaction,
guarded by the allocation's optimistic-concurrency `version` so two approvals
cannot both spend the same balance.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.time_off import TimeOffAllocation, TimeOffRequest, TimeOffType
from app.repositories.hr import (
    TimeOffAllocationRepository,
    TimeOffRequestRepository,
    TimeOffTypeRepository,
)
from app.services.base import BaseService


class TimeOffTypeService(BaseService[TimeOffType]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, TimeOffTypeRepository(session), entity_name="TimeOffType")


class TimeOffAllocationService(BaseService[TimeOffAllocation]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, TimeOffAllocationRepository(session), entity_name="TimeOffAllocation")


class TimeOffRequestService(BaseService[TimeOffRequest]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, TimeOffRequestRepository(session), entity_name="TimeOffRequest")
