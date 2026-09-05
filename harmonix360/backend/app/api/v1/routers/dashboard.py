"""Payroll dashboard (PS A7 / B9).

RBAC: the whole router requires `PAYROLL_ROLES` (Architecture §5) — HR Manager
gets no payroll access at all, and this endpoint aggregates `Payslip.net_amount`
across the organisation, so it sits behind the same gate `salary.py` and
`payroll.py` already use for payroll reads. See
`docs/dashboard-data-contract.md` §1 for why the whole response (including the
non-payroll widgets) is behind one gate rather than split.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import CurrentUser, require_role
from app.core.database import get_db
from app.core.rate_limit import rate_limiter
from app.models.enums import PAYROLL_ROLES, EmployeeType
from app.schemas.dashboard import DashboardResponse
from app.services.dashboard import DashboardService, resolve_filters

router = APIRouter(
    prefix="/dashboard", tags=["Dashboard"], dependencies=[Depends(rate_limiter)]
)


@router.get("/summary", response_model=DashboardResponse, summary="Payroll dashboard summary")
async def get_dashboard_summary(
    period_start: Optional[date] = Query(None, description="Inclusive. Defaults to the first day of the current month."),
    period_end: Optional[date] = Query(None, description="Inclusive. Defaults to the last day of the current month."),
    department_id: Optional[str] = Query(None),
    employee_type: Optional[EmployeeType] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    filters = await resolve_filters(
        db,
        period_start=period_start,
        period_end=period_end,
        department_id=department_id,
        employee_type=employee_type,
    )
    service = DashboardService(db)
    data = await service.get_dashboard(filters)
    return DashboardResponse(**data)
