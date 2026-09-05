"""Payroll services — Payrun and Payslip.

Phase 0: thin pass-throughs. Phase 5 adds the deterministic computation engine
(Architecture §7), and with it three obligations that are easy to forget and
expensive to miss:

  * `acquire_entity_lock(session, "payrun", payrun_id)` wraps Compute, because
    it rewrites the run's entire payslip set (Architecture §6).
  * `Idempotency-Key` is REQUIRED on Payrun create and compute — the middleware
    is what turns a double-clicked "Compute" into one payrun instead of two.
  * Every amount stays `Decimal` end to end and is stringified across any JSON
    boundary (Taskiq payload, MCP response, AI prompt context). No `float` ever
    touches this path.

And the rule that makes the AI layer safe to add later: nothing in app/ai or
app/mcp may call a write method here. The rule engine is the sole author of
every figure on a payslip (Architecture §7/§10).
"""
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payroll import Payrun, Payslip
from app.repositories.hr import PayrunRepository, PayslipRepository
from app.services.base import BaseService


class PayrunService(BaseService[Payrun]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, PayrunRepository(session), entity_name="Payrun")


class PayslipService(BaseService[Payslip]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, PayslipRepository(session), entity_name="Payslip")
