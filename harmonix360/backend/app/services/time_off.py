"""Time off policy and atomic approval (PS A4/B4).

Pending requests do not reserve leave. Approval selects one confirmed allocation
covering the entire request, earliest expiry first, with enough balance. Balances
are never split across allocations. The ORM's version_id_col protects BOTH the
allocation debit and request transition; a savepoint rolls both back on conflict.
Types without allocation skip that query entirely. Auto-approval uses the same
debit routine, but only the policy (never a client status) authorizes it.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.models.enums import TimeOffAllocationStatus as AS
from app.models.enums import TimeOffRequestStatus as RS
from app.models.enums import TimeOffUnit
from app.models.time_off import TimeOffAllocation, TimeOffRequest, TimeOffType
from app.repositories.hr import (
    TimeOffAllocationRepository,
    TimeOffRequestRepository,
    TimeOffTypeRepository,
)
from app.services.attendance import schedule_expectations
from app.realtime.events import time_off_decided, time_off_requested
from app.services.base import BaseService
from app.services.hr_access import (
    actor_id,
    employee_for,
    flush_or_conflict,
    require_hr,
    require_version,
)


def validate_dates(start, end):
    if end is not None and end < start:
        raise HTTPException(422, "End date must not precede start date")


async def get_type(session, public_id, *, editing=False):
    row = await TimeOffTypeRepository(session).get_by_public_id(public_id)
    if row is None:
        raise HTTPException(404, "Time off type not found")
    # Share-lock policy while creating references. Policy edits/deletion take
    # the exclusive counterpart before checking usage, closing the gap where
    # a concurrent first request could otherwise acquire a reinterpreted type.
    row = (
        await session.execute(
            select(TimeOffType)
            .where(TimeOffType.id == row.id, TimeOffType.deleted_at.is_(None))
            .with_for_update(read=not editing)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Time off type not found")
    return row


def request_duration(employee, leave_type, start, end):
    validate_dates(start, end)
    days = (end - start).days + 1
    if days > 3660:
        raise HTTPException(422, "Request cannot exceed ten years")
    if leave_type.unit == TimeOffUnit.DAYS:
        return Decimal(days).quantize(Decimal("0.01"))
    if (
        not employee.default_schedule
        or employee.default_schedule.deleted_at is not None
    ):
        raise HTTPException(
            422, "Hours-based leave requires an employee working schedule"
        )
    hours = sum(
        (
            schedule_expectations(employee, start + timedelta(days=i))[1] or Decimal(0)
            for i in range(days)
        ),
        Decimal(0),
    )
    if hours <= 0:
        raise HTTPException(422, "No scheduled working hours in this date range")
    return hours.quantize(Decimal("0.01"))


class TimeOffTypeService(BaseService[TimeOffType]):
    def __init__(self, session):
        super().__init__(
            session, TimeOffTypeRepository(session), entity_name="TimeOffType"
        )

    async def save_type(self, dto, user, public_id=None):
        require_hr(user)
        if public_id:
            row = await get_type(self.session, public_id, editing=True)
            require_version(row, dto.version)
            # Units/policy must not reinterpret existing allocations or requests.
            policy = (
                "unit",
                "requires_allocation",
                "requires_approval",
                "payroll_integration",
            )
            if any(getattr(row, key) != getattr(dto, key) for key in policy):
                await self._assert_unused(row)
            for key, value in dto.model_dump(exclude={"version"}).items():
                setattr(row, key, value)
            await flush_or_conflict(self.session)
        else:
            row = TimeOffType(public_id="temp", **dto.model_dump())
            try:
                await self.repo.create(row)
            except IntegrityError as exc:
                await self.session.rollback()
                raise HTTPException(409, "Time off type code already exists") from exc
        await self.audit(
            user.email,
            "SAVE_TIME_OFF_TYPE",
            row.public_id,
            after_diff=dto.model_dump(mode="json"),
        )
        return row

    async def _assert_unused(self, row):
        for model in (TimeOffAllocation, TimeOffRequest):
            found = (
                await self.session.execute(
                    select(model.id).where(model.time_off_type_id == row.id).limit(1)
                )
            ).scalar_one_or_none()
            if found:
                raise HTTPException(
                    409, "This type is in use; create a new type to change its policy"
                )

    async def delete_type(self, public_id, version, user):
        require_hr(user)
        row = await get_type(self.session, public_id, editing=True)
        require_version(row, version)
        await self._assert_unused(row)
        row.deleted_at = datetime.now(UTC)
        await flush_or_conflict(self.session)
        await self.audit(user.email, "DELETE_TIME_OFF_TYPE", row.public_id)
        return row


class TimeOffAllocationService(BaseService[TimeOffAllocation]):
    def __init__(self, session):
        super().__init__(
            session,
            TimeOffAllocationRepository(session),
            entity_name="TimeOffAllocation",
        )

    async def read(self, public_id, user):
        row = await self.get_or_404(public_id)
        await employee_for(self.session, row.employee.public_id, user)
        return row

    async def create_allocation(self, dto, user):
        require_hr(user)
        validate_dates(dto.valid_from, dto.valid_to)
        employee = await employee_for(self.session, dto.employee_id, user)
        leave_type = await get_type(self.session, dto.time_off_type_id)
        row = TimeOffAllocation(
            public_id="temp",
            employee=employee,
            time_off_type=leave_type,
            taken=Decimal(0),
            **dto.model_dump(exclude={"employee_id", "time_off_type_id"})
        )
        row.allocated = row.allocated.quantize(Decimal("0.01"))
        row.taken = Decimal("0.00")
        await self.repo.create(row)
        await self.audit(
            user.email,
            "CREATE_TIME_OFF_ALLOCATION",
            row.public_id,
            after_diff=dto.model_dump(mode="json"),
        )
        return row

    async def update_allocation(self, public_id, dto, user):
        require_hr(user)
        row = await self.get_or_404(public_id)
        require_version(row, dto.version)
        validate_dates(dto.valid_from, dto.valid_to)
        if dto.allocated < row.taken:
            raise HTTPException(
                409, "Allocated amount cannot be less than leave already taken"
            )
        if row.taken and (
            dto.valid_from != row.valid_from or dto.valid_to != row.valid_to
        ):
            raise HTTPException(
                409, "Validity of an allocation already used cannot change"
            )
        for key, value in dto.model_dump(exclude={"version"}).items():
            setattr(row, key, value)
        await flush_or_conflict(self.session)
        await self.audit(
            user.email,
            "UPDATE_TIME_OFF_ALLOCATION",
            row.public_id,
            after_diff=dto.model_dump(mode="json"),
        )
        return row

    async def delete_allocation(self, public_id, version, user):
        require_hr(user)
        row = await self.get_or_404(public_id)
        require_version(row, version)
        if row.taken:
            raise HTTPException(409, "An allocation already used cannot be deleted")
        row.deleted_at = datetime.now(UTC)
        await flush_or_conflict(self.session)
        await self.audit(user.email, "DELETE_TIME_OFF_ALLOCATION", row.public_id)
        return row


class TimeOffRequestService(BaseService[TimeOffRequest]):
    def __init__(self, session):
        super().__init__(
            session, TimeOffRequestRepository(session), entity_name="TimeOffRequest"
        )

    async def read(self, public_id, user):
        row = await self.get_or_404(public_id)
        await employee_for(self.session, row.employee.public_id, user)
        return row

    async def create_request(self, dto, user):
        employee = await employee_for(self.session, dto.employee_id, user)
        leave_type = await get_type(self.session, dto.time_off_type_id)
        duration = request_duration(employee, leave_type, dto.date_from, dto.date_to)
        try:
            async with self.session.begin_nested():
                row = TimeOffRequest(
                    public_id="temp",
                    employee=employee,
                    time_off_type=leave_type,
                    date_from=dto.date_from,
                    date_to=dto.date_to,
                    reason=dto.reason,
                    duration=duration,
                    status=RS.TO_APPROVE,
                )
                await self.repo.create(row)
                await self.audit(
                    user.email,
                    "CREATE_TIME_OFF_REQUEST",
                    row.public_id,
                    after_diff={"duration": str(duration), "status": row.status.value},
                )
                if not leave_type.requires_approval:
                    await self._approve(row, user, None, automatic=True)
            # Staged inside the transaction, dispatched by `get_db` after it
            # commits. An auto-approved type produces both events, in order.
            time_off_requested(self.session, row)
            if row.status is RS.APPROVED:
                time_off_decided(self.session, row)
            return row
        except StaleDataError as exc:
            raise HTTPException(
                409, "Allocation changed concurrently; refresh and try again"
            ) from exc

    async def update_request(self, public_id, dto, user):
        require_hr(user)
        row = await self.get_or_404(public_id)
        require_version(row, dto.version)
        self._require_pending(row)
        employee = await employee_for(self.session, dto.employee_id, user)
        leave_type = await get_type(self.session, dto.time_off_type_id)
        if not leave_type.requires_approval:
            raise HTTPException(
                409, "Submit a new request to use an automatic-approval type"
            )
        row.duration = request_duration(
            employee, leave_type, dto.date_from, dto.date_to
        )
        row.employee, row.time_off_type = employee, leave_type
        row.date_from, row.date_to, row.reason = dto.date_from, dto.date_to, dto.reason
        await flush_or_conflict(self.session)
        await self.audit(
            user.email,
            "UPDATE_TIME_OFF_REQUEST",
            row.public_id,
            after_diff=dto.model_dump(mode="json"),
        )
        return row

    async def decide(self, public_id, dto, user, *, approve):
        require_hr(user)  # Also guards direct service/MCP calls, before any DB read.
        try:
            async with self.session.begin_nested():
                row = await self.get_or_404(public_id)
                require_version(row, dto.version)
                self._require_pending(row)
                if approve:
                    await self._approve(row, user, dto.decision_note)
                else:
                    # No allocation read or write on refusal.
                    row.status = RS.REFUSED
                    row.approved_by = await actor_id(self.session, user)
                    row.decision_note = dto.decision_note
                    await self.session.flush()
                    await self.audit(
                        user.email,
                        "REFUSE_TIME_OFF_REQUEST",
                        row.public_id,
                        after_diff={"status": row.status.value},
                        reason=dto.decision_note,
                    )
            time_off_decided(self.session, row)
            return row
        except StaleDataError as exc:
            raise HTTPException(
                409, "Request or allocation changed concurrently; refresh and try again"
            ) from exc

    async def _matching_allocation(self, row):
        # Intentionally a versioned optimistic read, not SELECT FOR UPDATE.
        conditions = [
            TimeOffAllocation.employee_id == row.employee_id,
            TimeOffAllocation.time_off_type_id == row.time_off_type_id,
            TimeOffAllocation.deleted_at.is_(None),
            TimeOffAllocation.status == AS.CONFIRMED,
            TimeOffAllocation.valid_from <= row.date_from,
            or_(
                TimeOffAllocation.valid_to.is_(None),
                TimeOffAllocation.valid_to >= row.date_to,
            ),
        ]
        allocations = (
            (
                await self.session.execute(
                    select(TimeOffAllocation)
                    .where(*conditions)
                    .order_by(
                        TimeOffAllocation.valid_to.asc().nulls_last(),
                        TimeOffAllocation.id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not allocations:
            raise HTTPException(
                409, "No valid confirmed allocation covers the entire request"
            )
        for allocation in allocations:
            if allocation.remaining >= row.duration:
                return allocation
        raise HTTPException(409, "Insufficient leave balance in a matching allocation")

    async def _approve(self, row, user, note, *, automatic=False):
        if row.time_off_type.deleted_at is not None:
            raise HTTPException(409, "Time off type is no longer available")
        if row.time_off_type.requires_allocation:
            allocation = await self._matching_allocation(row)
            allocation.taken += row.duration
            row.allocation_id = allocation.id
        row.status = RS.APPROVED
        row.approved_by = None if automatic else await actor_id(self.session, user)
        row.decision_note = note
        # ORM flush includes version predicates on BOTH changed rows. A losing
        # request flush rolls its allocation debit back with the savepoint.
        await self.session.flush()
        await self.audit(
            user.email,
            (
                "AUTO_APPROVE_TIME_OFF_REQUEST"
                if automatic
                else "APPROVE_TIME_OFF_REQUEST"
            ),
            row.public_id,
            after_diff={"status": row.status.value, "duration": str(row.duration)},
            reason=note,
        )

    async def delete_request(self, public_id, version, user):
        require_hr(user)
        row = await self.get_or_404(public_id)
        require_version(row, version)
        if row.status == RS.APPROVED:
            raise HTTPException(
                409,
                "Approved leave is immutable; cancellation with balance credit is a future workflow",
            )
        row.deleted_at = datetime.now(UTC)
        await flush_or_conflict(self.session)
        await self.audit(user.email, "DELETE_TIME_OFF_REQUEST", row.public_id)
        return row

    @staticmethod
    def _require_pending(row):
        if row.status != RS.TO_APPROVE:
            raise HTTPException(409, "Only pending requests can be changed or decided")
