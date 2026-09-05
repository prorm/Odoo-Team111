"""Attendance service.

Phase 0: a thin pass-through. Phase 2 (PS B3) adds check-in/check-out, the
server-side worked-hours computation, and role-gated corrections. Phase 8
registers this entity for offline sync (check-in/out only, never corrections).
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attendance import Attendance
from app.repositories.hr import AttendanceRepository
from app.services.base import BaseService


class AttendanceService(BaseService[Attendance]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, AttendanceRepository(session), entity_name="Attendance")
