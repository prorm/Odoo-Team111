"""Salary configuration services — Structure and Rule.

Phase 0: thin pass-throughs. Phase 3 (PS A5/A6) adds rule ordering within a
structure and validation of the three computation methods.

RBAC note for whoever wires these up: Salary Structures and Rules are the one
pair in Architecture §5's matrix where read and write split across roles — HR
Payroll User is READ-ONLY, and only HR Payroll Manager (and Admin) may author.
Use PAYROLL_ROLES for the read endpoints and PAYROLL_ADMIN_ROLES for the
mutating ones; a single role set on the whole router would silently hand
authoring rights to the read-only role.
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.salary import SalaryRule, SalaryStructure
from app.repositories.hr import SalaryRuleRepository, SalaryStructureRepository
from app.services.base import BaseService


class SalaryStructureService(BaseService[SalaryStructure]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, SalaryStructureRepository(session), entity_name="SalaryStructure")


class SalaryRuleService(BaseService[SalaryRule]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, SalaryRuleRepository(session), entity_name="SalaryRule")
