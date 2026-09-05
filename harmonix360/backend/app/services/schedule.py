"""Working schedule service.

Phase 0: a thin pass-through. Phase 1 (PS A3) adds the rule that defines this
entity — weekly hours are computed HERE, server-side, from the schedule's
lines, and are never accepted from a request body or edited by hand.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.working_schedule import WorkingSchedule
from app.repositories.hr import WorkingScheduleRepository
from app.services.base import BaseService


class WorkingScheduleService(BaseService[WorkingSchedule]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, WorkingScheduleRepository(session), entity_name="WorkingSchedule")
