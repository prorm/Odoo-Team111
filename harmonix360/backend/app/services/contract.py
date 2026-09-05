"""Contract service (PS A2).

This service owes one obligation above all others, and BaseService's docstring
names it: `contracts` carries the `contracts_active_period_overlap_excl`
EXCLUDE constraint, so any mutation that can move a row into `status='active'`
can raise SQLSTATE 23P01 — and every such path must translate it into a clean
409, not just the obvious create.

There are THREE such paths here, and it is worth naming them because only the
first is obvious:

  1. `create_contract` with status='active'          — INSERT into the set
  2. `update_contract` changing dates on an active   — MOVE within the set
  3. `update_contract` flipping status to 'active'   — MOVE into the set

(3) is the trap: it looks like "just change a status" at the call site. All
three route through `_guard_overlap`, so adding a fourth mutation method means
wrapping it too.

The 409 message names the conflicting contract's period, because "this
overlaps" without saying what it overlaps leaves the user to hunt through a
contract history to find out.
"""
import logging
from datetime import date
from typing import List, Optional, Sequence, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contract import Contract
from app.models.employee import Employee
from app.models.enums import ContractStatus
from app.repositories.hr import (
    ContractRepository,
    DepartmentRepository,
    EmployeeRepository,
    SalaryStructureRepository,
    WorkingScheduleRepository,
)
from app.services.base import BaseService

logger = logging.getLogger("harmonix360.services.contract")

#: Postgres exclusion_violation — what the non-overlap constraint raises.
EXCLUSION_VIOLATION_SQLSTATE = "23P01"


class ContractService(BaseService[Contract]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, ContractRepository(session), entity_name="Contract")

    # --------------------------------------------------------------- reads

    async def list_contracts(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        employee_id: Optional[str] = None,
        contract_status: Optional[ContractStatus] = None,
        tenant_id: str = "default",
    ) -> Tuple[List[Contract], int]:
        conditions = [Contract.tenant_id == tenant_id, Contract.deleted_at.is_(None)]

        if employee_id:
            employee = await self._resolve_employee(employee_id)
            conditions.append(Contract.employee_id == employee.id)
        if contract_status:
            conditions.append(Contract.status == contract_status)

        total = (await self.session.execute(select(func.count(Contract.id)).where(*conditions))).scalar() or 0

        stmt = (
            select(Contract)
            .where(*conditions)
            # Most recent contract first: a contract list is read to answer
            # "what are they on now?", and the answer is at the top.
            .order_by(Contract.start_date.desc(), Contract.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, total

    async def active_contract_ids(self, contracts: Sequence[Contract], on: Optional[date] = None) -> set[int]:
        """Which of these contracts are the currently-active one for their
        employee (PS A2: "the list highlights the active contract").

        Computed server-side, in ONE query over the employees represented in
        the page, rather than derived in the browser. Two reasons:

          * A paginated list may not contain all of an employee's contracts, so
            a client-side answer would be confidently wrong on page two.
          * "Active" is `status='active' AND today ∈ [start, end]`, which is
            the same predicate the EXCLUDE constraint uses. Computing it in one
            place keeps the highlight and the constraint from ever disagreeing.

        The constraint guarantees at most one match per employee, so this
        cannot highlight two rows.
        """
        if not contracts:
            return set()

        as_of = on or date.today()
        employee_ids = {contract.employee_id for contract in contracts}

        stmt = select(Contract.id).where(
            Contract.employee_id.in_(employee_ids),
            Contract.status == ContractStatus.ACTIVE,
            Contract.deleted_at.is_(None),
            Contract.start_date <= as_of,
            or_(Contract.end_date.is_(None), Contract.end_date >= as_of),
        )
        return set((await self.session.execute(stmt)).scalars().all())

    # ------------------------------------------------------------- mutations

    async def create_contract(self, dto, *, actor_email: str) -> Contract:
        employee = await self._resolve_employee(dto.employee_id)

        contract = Contract(
            public_id="temp",
            employee_id=employee.id,
            wage=dto.wage,
            start_date=dto.start_date,
            end_date=dto.end_date,
            job_position=dto.job_position,
            status=dto.status,
            notes=dto.notes,
        )
        await self._apply_references(
            contract,
            department_id=dto.department_id,
            salary_structure_id=dto.salary_structure_id,
            working_schedule_id=dto.working_schedule_id,
        )

        async with self._guard_overlap(employee, contract.start_date, contract.end_date):
            created = await self.create(
                contract,
                actor=actor_email,
                action="CREATE_CONTRACT",
                after_diff={
                    "employee": employee.public_id,
                    "wage": str(contract.wage),
                    "start_date": str(contract.start_date),
                    "end_date": str(contract.end_date) if contract.end_date else None,
                    "status": contract.status.value,
                },
            )

        return await self._reload(created.id)

    async def update_contract(self, public_id: str, dto, *, actor_email: str) -> Contract:
        contract = await self.get_or_404(public_id)
        before = {
            "wage": str(contract.wage),
            "start_date": str(contract.start_date),
            "end_date": str(contract.end_date) if contract.end_date else None,
            "status": contract.status.value,
        }

        provided = dto.model_fields_set
        for field in ("wage", "start_date", "end_date", "job_position", "status", "notes"):
            if field in provided:
                setattr(contract, field, getattr(dto, field))

        # Validated AFTER the assignments, because a PATCH may move only one of
        # the two dates and the check has to see the resulting pair, not the
        # submitted one.
        if contract.end_date is not None and contract.end_date < contract.start_date:
            raise HTTPException(status_code=400, detail="end_date must not be before start_date")

        await self._apply_references(
            contract,
            department_id=dto.department_id if "department_id" in provided else _UNSET,
            salary_structure_id=dto.salary_structure_id if "salary_structure_id" in provided else _UNSET,
            working_schedule_id=dto.working_schedule_id if "working_schedule_id" in provided else _UNSET,
        )

        employee = await EmployeeRepository(self.session).get_by_id(contract.employee_id)

        # Guards paths (2) and (3) from the module docstring — a date change on
        # an active contract, and a status flip into active.
        async with self._guard_overlap(employee, contract.start_date, contract.end_date, exclude_id=contract.id):
            updated = await self.update(
                contract,
                actor=actor_email,
                action="UPDATE_CONTRACT",
                before_diff=before,
                after_diff={
                    "wage": str(contract.wage),
                    "start_date": str(contract.start_date),
                    "end_date": str(contract.end_date) if contract.end_date else None,
                    "status": contract.status.value,
                },
            )

        return await self._reload(updated.id)

    async def delete_contract(self, public_id: str, *, actor_email: str) -> Contract:
        contract = await self.get_or_404(public_id)
        return await self.soft_delete(contract, actor=actor_email, action="DELETE_CONTRACT")

    # -------------------------------------------------------------- internals

    def _guard_overlap(
        self,
        employee: Optional[Employee],
        start_date: date,
        end_date: Optional[date],
        exclude_id: Optional[int] = None,
    ):
        """Async context manager translating 23P01 into a useful 409.

        A context manager rather than a decorator so it can wrap exactly the
        flush — the audit write inside `create`/`update` is part of the same
        transaction, and rolling back has to discard both together.

        The employee's id and name are read OUT of the ORM object here, before
        the body runs. The rollback in `__aexit__` expires every instance in the
        session, so touching `employee.full_name` afterwards would trigger a
        lazy refresh from inside exception handling — which under asyncio is a
        `MissingGreenlet`, turning a clean 409 into a 500. Plain values survive
        the rollback; ORM instances do not.

        The conflicting contract is looked up only AFTER the constraint has
        fired. A pre-check would be a lie: between the SELECT and the INSERT,
        another request can commit a contract that makes ours invalid, and both
        would pass. The database is the only authority; this query exists purely
        to write a message a human can act on.
        """
        service = self
        employee_id = employee.id if employee is not None else None
        employee_name = employee.full_name if employee is not None else None

        class _Guard:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                if exc_type is None or not isinstance(exc, IntegrityError):
                    return False
                if getattr(exc.orig, "sqlstate", None) != EXCLUSION_VIOLATION_SQLSTATE:
                    return False

                await service.session.rollback()
                conflict = await service._find_conflicting_contract(
                    employee_id, start_date, end_date, exclude_id
                )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=service._overlap_message(employee_name, conflict, start_date, end_date),
                ) from exc

        return _Guard()

    async def _find_conflicting_contract(
        self,
        employee_id: Optional[int],
        start_date: date,
        end_date: Optional[date],
        exclude_id: Optional[int],
    ) -> Optional[dict]:
        """The conflicting contract, as plain values.

        Returns a dict rather than a Contract for the same reason the guard
        captures the employee's name up front: this runs immediately after a
        rollback, and anything the message needs must not require another
        attribute load once the caller starts formatting it.
        """
        if employee_id is None:
            return None

        # Mirrors the constraint's inclusive '[]' bounds: ranges overlap when
        # each one starts on or before the other ends. A NULL end_date is
        # unbounded, so it always satisfies its side.
        conditions = [
            Contract.employee_id == employee_id,
            Contract.status == ContractStatus.ACTIVE,
            Contract.deleted_at.is_(None),
            or_(Contract.end_date.is_(None), Contract.end_date >= start_date),
        ]
        if end_date is not None:
            conditions.append(Contract.start_date <= end_date)
        if exclude_id is not None:
            conditions.append(Contract.id != exclude_id)

        stmt = (
            select(Contract.public_id, Contract.start_date, Contract.end_date)
            .where(*conditions)
            .order_by(Contract.start_date)
            .limit(1)
        )
        row = (await self.session.execute(stmt)).first()
        if row is None:
            return None
        return {"public_id": row[0], "start_date": row[1], "end_date": row[2]}

    def _overlap_message(
        self,
        employee_name: Optional[str],
        conflict: Optional[dict],
        start_date: date,
        end_date: Optional[date],
    ) -> str:
        who = employee_name or "This employee"
        requested = f"{start_date} to {end_date or 'open-ended'}"

        if conflict is None:
            # The conflicting row was resolved away between the violation and
            # this lookup, or the employee could not be re-read. Say what is
            # known rather than inventing a period.
            return (
                f"{who} already has an active contract overlapping {requested}. "
                "Payroll must resolve exactly one contract per period, so end or cancel "
                "the existing contract before starting this one."
            )

        existing = f"{conflict['start_date']} to {conflict['end_date'] or 'open-ended'}"
        return (
            f"{who} already has an active contract ({conflict['public_id']}) covering {existing}, "
            f"which overlaps the requested period {requested}. Payroll must resolve exactly one "
            "contract per period — end or cancel the existing contract first, or choose "
            "non-overlapping dates."
        )

    async def _resolve_employee(self, public_id: str) -> Employee:
        employee = await EmployeeRepository(self.session).get_by_public_id(public_id)
        if employee is None:
            raise HTTPException(status_code=404, detail=f"Employee '{public_id}' not found")
        return employee

    async def _apply_references(
        self, contract: Contract, *, department_id, salary_structure_id, working_schedule_id
    ) -> None:
        if department_id is not _UNSET:
            if department_id:
                department = await DepartmentRepository(self.session).get_by_public_id(department_id)
                if department is None:
                    raise HTTPException(status_code=404, detail=f"Department '{department_id}' not found")
                contract.department_id = department.id
            else:
                contract.department_id = None

        if salary_structure_id is not _UNSET:
            if salary_structure_id:
                structure = await SalaryStructureRepository(self.session).get_by_public_id(salary_structure_id)
                if structure is None:
                    # The table exists but is empty until Phase 3; a 404 here
                    # names the real situation rather than letting an FK
                    # violation surface as a 500.
                    raise HTTPException(
                        status_code=404, detail=f"Salary structure '{salary_structure_id}' not found"
                    )
                contract.salary_structure_id = structure.id
            else:
                contract.salary_structure_id = None

        if working_schedule_id is not _UNSET:
            if working_schedule_id:
                schedule = await WorkingScheduleRepository(self.session).get_by_public_id(working_schedule_id)
                if schedule is None:
                    raise HTTPException(
                        status_code=404, detail=f"Working schedule '{working_schedule_id}' not found"
                    )
                contract.working_schedule_id = schedule.id
            else:
                contract.working_schedule_id = None

    async def _reload(self, internal_id: int) -> Contract:
        stmt = (
            select(Contract)
            .where(Contract.id == internal_id)
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one()


class _Unset:
    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<UNSET>"


_UNSET = _Unset()
