"""Shared service-level HR authorization and scoped reference resolution."""

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.models.user import User
from app.repositories.hr import EmployeeRepository


def require_hr(user):
    if not user.is_hr():
        raise HTTPException(403, "HR Manager or above required")


def require_version(entity, version):
    if entity.version != version:
        raise HTTPException(409, "Record changed; refresh and try again")


async def employee_for(session, public_id, user):
    if not user.is_hr() and public_id != user.employee_public_id:
        raise HTTPException(403, "Only your own employee records are accessible")
    employee = await EmployeeRepository(session).get_by_public_id(public_id or "")
    if employee is None:
        raise HTTPException(404, "Employee not found")
    return employee


async def actor_id(session, user):
    # Development fallback and token-only tests need not have a User row.
    return (
        await session.execute(select(User.id).where(User.email == user.email))
    ).scalar_one_or_none()


async def flush_or_conflict(session):
    try:
        await session.flush()
    except StaleDataError as exc:
        await session.rollback()
        raise HTTPException(
            409, "Record or allocation changed concurrently; refresh and try again"
        ) from exc
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(409, "Conflicting record or invalid balance") from exc


async def scoped_list(
    service, user, employee_id=None, *, own=False, limit=50, offset=0
):
    if own:
        employee_id = user.employee_public_id
        if not employee_id:
            raise HTTPException(404, "No employee linked to this login")
    else:
        require_hr(user)
    model = service.repo.model
    conditions = [model.deleted_at.is_(None)]
    if employee_id:
        employee = await employee_for(service.session, employee_id, user)
        conditions.append(model.employee_id == employee.id)
    total = (
        await service.session.execute(select(func.count(model.id)).where(*conditions))
    ).scalar_one()
    rows = (
        (
            await service.session.execute(
                select(model)
                .where(*conditions)
                .order_by(model.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return rows, total
