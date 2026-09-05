"""Employee service (PS A1/B2).

Two things here are worth reading before changing anything:

  * **Reference resolution.** Every foreign key arrives as a public_id and is
    resolved through the owning repository, which fails closed on an id of the
    wrong entity type (`decode_public_id` refuses a mismatched prefix). A raw
    integer FK from a request body would let a client point an employee at a
    row they were never shown.

  * **Row scoping.** Architecture §5 gives an Employee read access to their own
    record and nothing else. That is not expressible as a role check, so
    `assert_can_read` compares against the token's signed `employee_id` claim.
    Every read path that an Employee can reach must go through it.
"""
import logging
from typing import List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.v1.deps import CurrentUser
from app.models.attendance import Attendance
from app.models.contract import Contract
from app.models.department import Department
from app.models.employee import Employee
from app.models.enums import EmployeeStatus, EmployeeType
from app.models.time_off import TimeOffAllocation, TimeOffRequest
from app.models.working_schedule import WorkingSchedule
from app.repositories.hr import (
    DepartmentRepository,
    EmployeeRepository,
    WorkingScheduleRepository,
)
from app.schemas.employee import SmartButtonCounts
from app.services.base import BaseService

logger = logging.getLogger("harmonix360.services.employee")

UNIQUE_VIOLATION_SQLSTATE = "23505"


class EmployeeService(BaseService[Employee]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, EmployeeRepository(session), entity_name="Employee")

    # --------------------------------------------------------------- reads

    async def list_employees(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        search: Optional[str] = None,
        department_id: Optional[str] = None,
        employee_status: Optional[EmployeeStatus] = None,
        employee_type: Optional[EmployeeType] = None,
        exclude_public_id: Optional[str] = None,
        tenant_id: str = "default",
    ) -> Tuple[List[Employee], int]:
        """Filtered, paginated list. Backs the List view, the Kanban and the
        manager picker alike — one query shape rather than three, because they
        differ only in which filters they set.

        `exclude_public_id` exists for the manager picker: an employee must not
        be offered as their own manager, and filtering that out server-side
        means the UI cannot forget to.
        """
        conditions = [Employee.tenant_id == tenant_id, Employee.deleted_at.is_(None)]

        if search:
            # ILIKE, not a full-text index: an employee directory is thousands
            # of rows, not millions, and a trigram index would be tuning ahead
            # of a measurement.
            pattern = f"%{search.strip()}%"
            conditions.append(
                or_(
                    Employee.first_name.ilike(pattern),
                    Employee.last_name.ilike(pattern),
                    Employee.work_email.ilike(pattern),
                    Employee.job_position.ilike(pattern),
                )
            )

        if department_id:
            department = await self._resolve_department(department_id)
            conditions.append(Employee.department_id == department.id)

        if employee_status:
            conditions.append(Employee.status == employee_status)
        if employee_type:
            conditions.append(Employee.employee_type == employee_type)

        if exclude_public_id:
            excluded = await self.repo.get_by_public_id(exclude_public_id)
            if excluded is not None:
                conditions.append(Employee.id != excluded.id)

        total = (await self.session.execute(select(func.count(Employee.id)).where(*conditions))).scalar() or 0

        stmt = (
            select(Employee)
            .where(*conditions)
            .options(
                selectinload(Employee.department),
                selectinload(Employee.manager),
                selectinload(Employee.default_schedule),
            )
            # Alphabetical, not id-descending: this is a directory people scan
            # by name, and "most recently added first" is useful to nobody
            # looking for a colleague.
            .order_by(Employee.first_name, Employee.last_name, Employee.id)
            .limit(limit)
            .offset(offset)
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return list(rows), total

    async def smart_button_counts(self, employee: Employee) -> SmartButtonCounts:
        """Counts for the Employee form's smart buttons (PS B2).

        Four scalar subqueries in ONE round trip rather than four sequential
        counts. Attendance and Time Off have no data before Phases 2-3; they
        legitimately return 0 now and start returning real numbers the moment
        those phases insert rows, with no change here — which is the point of
        wiring the pattern in this phase.
        """

        def _count(model) -> select:
            return (
                select(func.count(model.id))
                .where(model.employee_id == employee.id, model.deleted_at.is_(None))
                .scalar_subquery()
            )

        row = (
            await self.session.execute(
                select(
                    _count(Contract),
                    _count(Attendance),
                    _count(TimeOffRequest),
                    _count(TimeOffAllocation),
                )
            )
        ).one()

        return SmartButtonCounts(
            contracts=row[0],
            attendance=row[1],
            time_off_requests=row[2],
            time_off_allocations=row[3],
        )

    # ------------------------------------------------------------- mutations

    async def create_employee(self, dto, *, actor_email: str) -> Employee:
        employee = Employee(
            public_id="temp",
            first_name=dto.first_name,
            last_name=dto.last_name,
            work_email=dto.work_email,
            phone=dto.phone,
            job_position=dto.job_position,
            employee_type=dto.employee_type,
            status=dto.status,
            hire_date=dto.hire_date,
            exit_date=dto.exit_date,
            bank_account=dto.bank_account,
        )
        await self._apply_references(
            employee,
            department_id=dto.department_id,
            manager_id=dto.manager_id,
            default_schedule_id=dto.default_schedule_id,
        )

        try:
            created = await self.create(
                employee,
                actor=actor_email,
                action="CREATE_EMPLOYEE",
                after_diff={
                    "work_email": employee.work_email,
                    "full_name": employee.full_name,
                    "status": employee.status.value,
                },
            )
        except IntegrityError as exc:
            await self.session.rollback()
            self._translate_unique_violation(exc, dto.work_email)
            raise

        return await self._reload(created.id)

    async def update_employee(self, public_id: str, dto, *, actor_email: str) -> Employee:
        employee = await self.get_or_404(public_id)
        before = {
            "work_email": employee.work_email,
            "full_name": employee.full_name,
            "status": employee.status.value,
            "job_position": employee.job_position,
        }

        # `model_fields_set` rather than "is not None": it is what distinguishes
        # a field the client omitted (leave it alone) from one it explicitly set
        # to null (clear it). Without that distinction, clearing a manager would
        # be impossible and every PATCH would wipe every unmentioned field.
        provided = dto.model_fields_set
        for field in (
            "first_name",
            "last_name",
            "work_email",
            "phone",
            "job_position",
            "employee_type",
            "status",
            "hire_date",
            "exit_date",
            "bank_account",
        ):
            if field in provided:
                setattr(employee, field, getattr(dto, field))

        await self._apply_references(
            employee,
            department_id=dto.department_id if "department_id" in provided else _UNSET,
            manager_id=dto.manager_id if "manager_id" in provided else _UNSET,
            default_schedule_id=dto.default_schedule_id if "default_schedule_id" in provided else _UNSET,
        )

        try:
            updated = await self.update(
                employee,
                actor=actor_email,
                action="UPDATE_EMPLOYEE",
                before_diff=before,
                after_diff={
                    "work_email": employee.work_email,
                    "full_name": employee.full_name,
                    "status": employee.status.value,
                    "job_position": employee.job_position,
                },
            )
        except IntegrityError as exc:
            await self.session.rollback()
            self._translate_unique_violation(exc, employee.work_email)
            raise

        return await self._reload(updated.id)

    async def delete_employee(self, public_id: str, *, actor_email: str) -> Employee:
        """Soft delete. Never a hard one: payslips and audit rows reference this
        employee, and an HR record is evidence of an employment relationship
        that outlives the employment."""
        employee = await self.get_or_404(public_id)
        return await self.soft_delete(employee, actor=actor_email, action="DELETE_EMPLOYEE")

    # ------------------------------------------------------------ row scoping

    def assert_can_read(self, current_user: CurrentUser, employee: Employee) -> None:
        """Architecture §5, first row: an Employee may read their OWN record.

        HR Manager and above pass on their role. An Employee passes only when
        the record's public_id matches the `employee_id` claim in their token —
        a value the server signed, never one the request supplied.

        404, not 403, on a mismatch: a 403 confirms the record exists, which
        would let anyone with a login enumerate the employee directory by
        probing ids. There is nothing to gain from telling them.
        """
        if current_user.is_hr():
            return
        if current_user.employee_public_id and current_user.employee_public_id == employee.public_id:
            return
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Employee not found")

    # -------------------------------------------------------------- internals

    async def _apply_references(
        self,
        employee: Employee,
        *,
        department_id,
        manager_id,
        default_schedule_id,
    ) -> None:
        if department_id is not _UNSET:
            employee.department_id = (
                (await self._resolve_department(department_id)).id if department_id else None
            )

        if manager_id is not _UNSET:
            if manager_id:
                manager = await self.repo.get_by_public_id(manager_id)
                if manager is None:
                    raise HTTPException(status_code=404, detail=f"Manager '{manager_id}' not found")
                if employee.id is not None and manager.id == employee.id:
                    # The immediate self-loop only. A longer cycle
                    # (A -> B -> A) is not checked here: detecting it needs a
                    # recursive walk on every save, and the UI cannot construct
                    # one in a single edit. If org-chart traversal is added
                    # later, that walk is where the guard belongs.
                    raise HTTPException(status_code=400, detail="An employee cannot be their own manager")
                employee.manager_id = manager.id
            else:
                employee.manager_id = None

        if default_schedule_id is not _UNSET:
            if default_schedule_id:
                schedule = await WorkingScheduleRepository(self.session).get_by_public_id(default_schedule_id)
                if schedule is None:
                    raise HTTPException(
                        status_code=404, detail=f"Working schedule '{default_schedule_id}' not found"
                    )
                employee.default_schedule_id = schedule.id
            else:
                employee.default_schedule_id = None

    async def _resolve_department(self, public_id: str) -> Department:
        department = await DepartmentRepository(self.session).get_by_public_id(public_id)
        if department is None:
            raise HTTPException(status_code=404, detail=f"Department '{public_id}' not found")
        return department

    def _translate_unique_violation(self, exc: IntegrityError, work_email: str) -> None:
        """(work_email, tenant_id) is unique. Surface it as a 409 naming the
        field, not a 500 — this is the single most likely error when creating
        an employee, and "internal server error" tells the user nothing about
        the address they need to change."""
        if getattr(exc.orig, "sqlstate", None) != UNIQUE_VIOLATION_SQLSTATE:
            return
        detail = str(exc.orig)
        if "uq_employee_work_email_tenant" in detail:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"An employee with the work email '{work_email}' already exists",
            ) from exc
        if "uq_employee_user_id" in detail:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="That user account is already linked to another employee",
            ) from exc

    async def _reload(self, internal_id: int) -> Employee:
        """Re-read with the response's relationships eagerly loaded.

        The eager loads are spelled out rather than left to the model's
        `lazy="selectin"` defaults, because `populate_existing=True` refreshes
        the instance and re-expires its relationship attributes. Touching one
        afterwards would then trigger a lazy load from inside Pydantic's
        synchronous attribute read — which under asyncio is a `MissingGreenlet`
        rather than a query, and surfaces as a 500 on an otherwise successful
        write.
        """
        stmt = (
            select(Employee)
            .where(Employee.id == internal_id)
            .options(
                selectinload(Employee.department),
                selectinload(Employee.manager),
                selectinload(Employee.default_schedule),
            )
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one()


class _Unset:
    """Sentinel distinguishing 'the client omitted this field' from 'the client
    sent null'. `None` cannot do that job here, because null is a meaningful
    value for every one of these three references — it means "clear it"."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<UNSET>"


_UNSET = _Unset()
