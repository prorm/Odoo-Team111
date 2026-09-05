"""Departments — read-only (Architecture §4: "reuse existing model as-is").

Exposed so the Employee and Contract forms have something to populate their
department picker from. Read-only on purpose: Architecture §5's matrix has no
row for Departments, which means nothing in PS §4 asks for department
management, and inventing CRUD here would be building an admin screen the
problem statement never requested. Departments are seeded (app/seed.py); if
managing them becomes a requirement, it belongs to Admin.

Readable by any authenticated user: a department name is org-chart
information, not personnel data, and an employee needs to see their own
department on their own profile.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import CurrentUser, require_role
from app.core.database import get_db
from app.core.rate_limit import rate_limiter
from app.models.department import Department
from app.models.enums import ALL_ROLES, DepartmentStatus
from app.schemas.employee import DepartmentRef

router = APIRouter(prefix="/departments", tags=["Departments"], dependencies=[Depends(rate_limiter)])


@router.get("/", response_model=list[DepartmentRef], summary="List departments")
async def list_departments(
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(ALL_ROLES)),
):
    """Returned unpaginated (up to `limit`) because this feeds a picker, and a
    paginated dropdown that silently omits a department is worse than a long
    one. An organisation with more than 500 departments has a different
    problem."""
    stmt = (
        select(Department)
        .where(Department.deleted_at.is_(None), Department.status == DepartmentStatus.ACTIVE)
        .order_by(Department.name)
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [DepartmentRef.model_validate(row) for row in rows]
