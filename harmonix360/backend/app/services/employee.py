"""Employee service.

Phase 0: a thin pass-through over BaseRepository — `list`, `get_or_404`,
`create`, `update`, `soft_delete` all come from BaseService, already wired to
the audit log. Phase 1 (PS A1/B2) adds the real behaviour: manager search,
Kanban grouping, the smart-button counts, and the "an Employee sees only their
own record" row scoping.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.employee import Employee
from app.repositories.hr import EmployeeRepository
from app.services.base import BaseService


class EmployeeService(BaseService[Employee]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, EmployeeRepository(session), entity_name="Employee")
