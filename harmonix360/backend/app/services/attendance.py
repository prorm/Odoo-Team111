"""Attendance operations, including service-level authorization.

UTC is the organization clock. Worked hours are elapsed hours. Precedence:
missing checkout, absent (zero hours), overtime, late, present. Reads derive
status again so yesterday's open entry cannot stay present forever.
"""

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.attendance import Attendance
from app.models.enums import WEEKDAY_ORDER, AttendanceStatus
from app.repositories.hr import AttendanceRepository
from app.realtime.events import attendance_recorded
from app.services.base import BaseService
from app.services.hr_access import (
    actor_id,
    employee_for,
    flush_or_conflict,
    require_hr,
    require_version,
)


def worked_hours(check_in, check_out):
    if check_out is None:
        return None
    if check_out < check_in:
        raise ValueError("Check out must not precede check in")
    delta = check_out - check_in
    seconds = (
        Decimal(delta.days * 86400 + delta.seconds)
        + Decimal(delta.microseconds) / 1000000
    )
    return (seconds / 3600).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def derive_attendance_status(
    check_in, check_out, *, now, expected_start=None, expected_hours=None
):
    """Pure, clock-injected status policy; no DB, clock reads, or mutation."""
    if check_in is None:
        return AttendanceStatus.ABSENT
    hours = worked_hours(check_in, check_out)
    if (
        check_out is None
        and check_in.astimezone(UTC).date() < now.astimezone(UTC).date()
    ):
        return AttendanceStatus.MISSING_CHECKOUT
    if check_out == check_in:
        return AttendanceStatus.ABSENT
    if hours is not None and expected_hours is not None and hours > expected_hours:
        return AttendanceStatus.OVERTIME
    if expected_start is not None and check_in > expected_start:
        return AttendanceStatus.LATE
    return AttendanceStatus.PRESENT


def schedule_expectations(employee, day, *, schedule=None):
    """Expected start time and net hours for one employee on one day.

    `schedule` overrides the employee's default. Attendance itself never
    passes one — an employee checks in against their own schedule — but
    payroll does: a Contract may override the schedule for the period it
    covers (PS A3, `Contract.working_schedule_id`), and the payroll context
    must derive attendance status against the same schedule the contract
    being paid actually names. Defaulting to the employee's own schedule
    keeps every Phase 2 caller unchanged.
    """
    schedule = schedule if schedule is not None else employee.default_schedule
    if not schedule or schedule.deleted_at is not None:
        return None, None
    lines = [
        line
        for line in schedule.lines
        if WEEKDAY_ORDER[line.day_of_week] == day.weekday()
    ]
    if not lines:
        return None, Decimal(0)
    start = datetime.combine(day, min(line.start_time for line in lines), UTC)
    hours = sum(
        (
            worked_hours(
                datetime.combine(day, line.start_time, UTC),
                datetime.combine(day, line.end_time, UTC),
            )
            - Decimal(line.break_minutes) / 60
            for line in lines
        ),
        Decimal(0),
    )
    return start, hours


class AttendanceService(BaseService[Attendance]):
    def __init__(self, session: AsyncSession):
        super().__init__(
            session, AttendanceRepository(session), entity_name="Attendance"
        )

    def status_for(self, row, *, now=None):
        start, hours = schedule_expectations(
            row.employee, row.check_in.astimezone(UTC).date()
        )
        return derive_attendance_status(
            row.check_in,
            row.check_out,
            now=now or datetime.now(UTC),
            expected_start=start,
            expected_hours=hours,
        )

    async def read(self, public_id, user):
        row = await self.get_or_404(public_id)
        await employee_for(self.session, row.employee.public_id, user)
        return row

    async def create_attendance(self, dto, user):
        employee = await employee_for(self.session, dto.employee_id, user)
        row = Attendance(
            public_id="temp",
            employee=employee,
            check_in=dto.check_in,
            check_out=dto.check_out,
        )
        self._compute(row)
        await self.repo.create(row)
        await self.audit(
            user.email,
            "CREATE_ATTENDANCE",
            row.public_id,
            after_diff=self._snapshot(row),
        )
        # Staged only. `get_db` broadcasts it after this transaction commits
        # (Architecture §8.4); a rolled-back check-in is never announced.
        attendance_recorded(self.session, row, action="checked_in")
        return row

    async def check_in(self, employee_id, user):
        from app.schemas.attendance import AttendanceCreate

        return await self.create_attendance(
            AttendanceCreate(employee_id=employee_id, check_in=datetime.now(UTC)), user
        )

    async def check_out(self, public_id, dto, user):
        row = await self.read(public_id, user)
        require_version(row, dto.version)
        if row.check_out is not None:
            raise HTTPException(
                409, "Already checked out; an HR correction is required"
            )
        row.check_out = datetime.now(UTC)
        self._compute(row)
        await flush_or_conflict(self.session)
        await self.audit(
            user.email, "CHECK_OUT", row.public_id, after_diff=self._snapshot(row)
        )
        attendance_recorded(self.session, row, action="checked_out")
        return row

    async def correct(self, public_id, dto, user):
        require_hr(user)
        row = await self.get_or_404(public_id)
        require_version(row, dto.version)
        if not dto.correction_reason.strip():
            raise HTTPException(422, "A correction reason is required")
        before = self._snapshot(row)
        row.check_in, row.check_out = dto.check_in, dto.check_out
        row.corrected_by = await actor_id(self.session, user)
        row.correction_reason = dto.correction_reason.strip()
        self._compute(row)
        await flush_or_conflict(self.session)
        await self.audit(
            user.email,
            "CORRECT_ATTENDANCE",
            row.public_id,
            before_diff=before,
            after_diff=self._snapshot(row),
            reason=row.correction_reason,
        )
        attendance_recorded(self.session, row, action="corrected")
        return row

    async def delete_attendance(self, public_id, version, user):
        require_hr(user)
        row = await self.get_or_404(public_id)
        require_version(row, version)
        row.deleted_at = datetime.now(UTC)
        await flush_or_conflict(self.session)
        await self.audit(user.email, "DELETE_ATTENDANCE", row.public_id)
        return row

    def _compute(self, row):
        try:
            row.worked_hours = worked_hours(row.check_in, row.check_out)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if row.worked_hours is not None and row.worked_hours >= 10000:
            raise HTTPException(422, "Attendance interval is too long")
        row.status = self.status_for(row)

    @staticmethod
    def _snapshot(row):
        return {
            "check_in": row.check_in.isoformat(),
            "check_out": row.check_out.isoformat() if row.check_out else None,
            "worked_hours": str(row.worked_hours),
            "status": row.status.value,
        }
