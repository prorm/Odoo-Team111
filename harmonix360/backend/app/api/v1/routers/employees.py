"""Employees (PS A1/B2).

RBAC, Architecture §5:

    Employees module        HR Manager and above: full CRUD
    Own profile             Employee: read, and only their own row

Those are two different rules, so they are two different routes. The collection
endpoints below are HR-only; an Employee reaches their own record through
`GET /employees/me` and `GET /employees/{id}` (which row-scopes). Widening the
collection to `ALL_ROLES` would expose the whole staff directory — job titles,
work emails, bank accounts — to every logged-in employee.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import CurrentUser, get_current_user, require_role
from app.core.database import get_db
from app.core.rate_limit import rate_limiter
from app.models.employee import Employee
from app.models.enums import ALL_ROLES, HR_ROLES, EmployeeStatus, EmployeeType
from app.schemas.common import PaginatedResponse
from app.schemas.employee import (
    EmployeeCreate,
    EmployeeRef,
    EmployeeResponse,
    EmployeeUpdate,
    SmartButtonCounts,
)
from app.services.employee import EmployeeService

router = APIRouter(prefix="/employees", tags=["Employees"], dependencies=[Depends(rate_limiter)])


def _to_response(employee: Employee) -> EmployeeResponse:
    return EmployeeResponse.model_validate(employee)


@router.get("/", response_model=PaginatedResponse[EmployeeResponse], summary="List employees")
async def list_employees(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None, description="Matches name, work email or job position"),
    department_id: Optional[str] = Query(None),
    status_filter: Optional[EmployeeStatus] = Query(None, alias="status"),
    employee_type: Optional[EmployeeType] = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(HR_ROLES)),
):
    """Backs the List view and the Kanban board alike.

    The Kanban groups client-side from this same response rather than through a
    separate grouped endpoint: the board shows one page of employees, the
    grouping key is a field already on every row, and a second endpoint would
    be a second place for the filters to drift.
    """
    service = EmployeeService(db)
    rows, total = await service.list_employees(
        limit=limit,
        offset=offset,
        search=search,
        department_id=department_id,
        employee_status=status_filter,
        employee_type=employee_type,
    )
    return PaginatedResponse(
        items=[_to_response(row) for row in rows], total=total, limit=limit, offset=offset
    )


@router.get("/lookup", response_model=list[EmployeeRef], summary="Employee picker options")
async def lookup_employees(
    search: Optional[str] = Query(None),
    exclude: Optional[str] = Query(None, description="Employee public_id to omit, e.g. the one being edited"),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(HR_ROLES)),
):
    """Options for the manager picker on the Employee form.

    Separate from `GET /` and returning `EmployeeRef`, not the full record: a
    picker lists every colleague, so it must carry only what a chip needs.
    Reusing the full response here would put bank accounts and phone numbers
    behind a dropdown.

    `exclude` drops the employee being edited, so nobody can be their own
    manager — enforced again in the service, since the query parameter is a
    convenience the client could omit.
    """
    service = EmployeeService(db)
    rows, _ = await service.list_employees(
        limit=limit, offset=0, search=search, exclude_public_id=exclude
    )
    return [EmployeeRef.model_validate(row) for row in rows]


@router.get("/me", response_model=EmployeeResponse, summary="The caller's own employee record")
async def read_own_employee(
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(ALL_ROLES)),
):
    """Architecture §5's first row. Resolves from the token's signed
    `employee_id` claim — never from a query parameter, which would let anyone
    ask for somebody else's record through the self-service route."""
    from fastapi import HTTPException

    if not current_user.employee_public_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This login is not linked to an employee record",
        )
    service = EmployeeService(db)
    return _to_response(await service.get_or_404(current_user.employee_public_id))


@router.post("/", response_model=EmployeeResponse, status_code=status.HTTP_201_CREATED)
async def create_employee(
    dto: EmployeeCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(HR_ROLES)),
):
    service = EmployeeService(db)
    return _to_response(await service.create_employee(dto, actor_email=current_user.email))


@router.get("/{public_id}", response_model=EmployeeResponse)
async def get_employee(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(ALL_ROLES)),
):
    """Readable by HR and above for anyone; by an Employee for themselves only.

    The role gate here is ALL_ROLES on purpose — the real check is
    `assert_can_read`, which is row-level and cannot be expressed as a role.
    """
    service = EmployeeService(db)
    employee = await service.get_or_404(public_id)
    service.assert_can_read(current_user, employee)
    return _to_response(employee)


@router.get("/{public_id}/counts", response_model=SmartButtonCounts)
async def get_smart_button_counts(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(ALL_ROLES)),
):
    """Counts behind the Employee form's smart buttons (PS B2).

    Row-scoped identically to the record itself: the number of contracts
    someone holds is information about them.
    """
    service = EmployeeService(db)
    employee = await service.get_or_404(public_id)
    service.assert_can_read(current_user, employee)
    return await service.smart_button_counts(employee)


@router.patch("/{public_id}", response_model=EmployeeResponse)
async def update_employee(
    public_id: str,
    dto: EmployeeUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(HR_ROLES)),
):
    """HR-only, including for one's own record. An employee editing their own
    job position, status or bank account is exactly the change that must go
    through HR."""
    service = EmployeeService(db)
    return _to_response(await service.update_employee(public_id, dto, actor_email=current_user.email))


@router.delete("/{public_id}", response_model=EmployeeResponse)
async def delete_employee(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(HR_ROLES)),
):
    service = EmployeeService(db)
    return _to_response(await service.delete_employee(public_id, actor_email=current_user.email))
