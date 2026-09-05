"""Working schedule service (PS A3).

The rule this service exists to enforce: **weekly hours are auto-computed,
never manually entered.** `compute_weekly_hours` is the only writer of
`WorkingSchedule.weekly_hours`, it runs on every create and on every line edit,
and the request schemas do not carry the field at all — so there is no path,
valid or invalid, by which a client sets it.

That matters beyond tidiness: weekly hours are a payroll input. A schedule
whose stated hours disagree with its lines produces payslips nobody can
reconcile against the pattern the employee actually works.
"""
import logging
from decimal import Decimal
from typing import List, Optional, Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import WEEKDAY_ORDER, WorkingScheduleType
from app.models.working_schedule import ScheduleLine, WorkingSchedule
from app.repositories.hr import ScheduleLineRepository, WorkingScheduleRepository
from app.schemas.schedule import ScheduleLineInput
from app.services.base import BaseService

logger = logging.getLogger("harmonix360.services.schedule")

MINUTES_PER_HOUR = Decimal("60")


def compute_weekly_hours(lines: Sequence[ScheduleLine | ScheduleLineInput]) -> Decimal:
    """Total paid hours in one week, from the schedule's blocks.

    Each block contributes (end - start) minus its unpaid break. Arithmetic is
    done in whole MINUTES and converted to hours exactly once, at the end:
    converting per line and summing would round each line to two decimals
    first, so a schedule of five 7h35m days would land a few minutes off the
    truth. Minutes are integers, so the only rounding in the whole calculation
    is the single final division.

    Decimal throughout, never float — this feeds an hourly rate.
    """
    total_minutes = 0
    for line in lines:
        start = line.start_time.hour * 60 + line.start_time.minute
        end = line.end_time.hour * 60 + line.end_time.minute
        block = end - start - (line.break_minutes or 0)
        # Defensive: the schemas reject these, but this function is also called
        # with ORM rows loaded from a database that predates that validation.
        if block > 0:
            total_minutes += block

    return (Decimal(total_minutes) / MINUTES_PER_HOUR).quantize(Decimal("0.01"))


class WorkingScheduleService(BaseService[WorkingSchedule]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, WorkingScheduleRepository(session), entity_name="WorkingSchedule")
        self.line_repo = ScheduleLineRepository(session)

    async def list_with_lines(
        self, limit: int = 50, offset: int = 0, tenant_id: str = "default"
    ) -> tuple[List[WorkingSchedule], int]:
        # `lines` is a selectin relationship, so the base list already loads
        # them in one extra query rather than N.
        return await self.repo.list_active(limit=limit, offset=offset, tenant_id=tenant_id)

    async def create_schedule(
        self,
        *,
        name: str,
        schedule_type: WorkingScheduleType,
        lines: Sequence[ScheduleLineInput],
        actor_email: str,
    ) -> WorkingSchedule:
        schedule = WorkingSchedule(
            public_id="temp",
            name=name,
            schedule_type=schedule_type,
            weekly_hours=compute_weekly_hours(lines),
        )
        created = await self.create(
            schedule,
            actor=actor_email,
            action="CREATE_WORKING_SCHEDULE",
            after_diff={
                "name": name,
                "schedule_type": schedule_type.value,
                "weekly_hours": str(schedule.weekly_hours),
                "line_count": len(lines),
            },
        )
        await self._replace_lines(created, lines)
        return await self._reload(created.id)

    async def update_schedule(
        self,
        public_id: str,
        *,
        name: Optional[str] = None,
        schedule_type: Optional[WorkingScheduleType] = None,
        lines: Optional[Sequence[ScheduleLineInput]] = None,
        actor_email: str,
    ) -> WorkingSchedule:
        schedule = await self.get_or_404(public_id)
        before = {
            "name": schedule.name,
            "schedule_type": schedule.schedule_type.value,
            "weekly_hours": str(schedule.weekly_hours),
        }

        if name is not None:
            schedule.name = name
        if schedule_type is not None:
            schedule.schedule_type = schedule_type

        if lines is not None:
            # Recomputed from the INCOMING lines, before they are written, so
            # the stored total and the stored lines are set in one transaction
            # and can never disagree.
            schedule.weekly_hours = compute_weekly_hours(lines)

        updated = await self.update(
            schedule,
            actor=actor_email,
            action="UPDATE_WORKING_SCHEDULE",
            before_diff=before,
            after_diff={
                "name": schedule.name,
                "schedule_type": schedule.schedule_type.value,
                "weekly_hours": str(schedule.weekly_hours),
            },
        )

        if lines is not None:
            await self._replace_lines(updated, lines)

        return await self._reload(updated.id)

    async def delete_schedule(self, public_id: str, *, actor_email: str) -> WorkingSchedule:
        schedule = await self.get_or_404(public_id)
        return await self.soft_delete(schedule, actor=actor_email, action="DELETE_WORKING_SCHEDULE")

    # ------------------------------------------------------------- internals

    async def _replace_lines(self, schedule: WorkingSchedule, lines: Sequence[ScheduleLineInput]) -> None:
        """Delete the schedule's lines and write the new set.

        A hard DELETE, not a soft one: ScheduleLine has no `deleted_at` (it is
        a line-item child, Architecture §4), and the
        (schedule_id, day_of_week, start_time) unique constraint means a
        soft-deleted row would block re-adding the same block later.

        Lines are inserted in canonical weekday order so the form renders
        Monday-first without the client sorting, and so two schedules with the
        same pattern have the same row order in the database.
        """
        await self.session.execute(delete(ScheduleLine).where(ScheduleLine.schedule_id == schedule.id))

        ordered = sorted(lines, key=lambda line: (WEEKDAY_ORDER[line.day_of_week], line.start_time))
        for line in ordered:
            await self.line_repo.create(
                ScheduleLine(
                    public_id="temp",
                    schedule_id=schedule.id,
                    day_of_week=line.day_of_week,
                    start_time=line.start_time,
                    end_time=line.end_time,
                    break_minutes=line.break_minutes,
                )
            )
        await self.session.flush()

    async def _reload(self, internal_id: int) -> WorkingSchedule:
        """Re-read with `lines` freshly populated.

        `populate_existing` is required: the schedule instance in this session
        already has a `lines` collection loaded from before `_replace_lines`
        ran, and a plain select would hand that stale collection back rather
        than the rows just written.
        """
        stmt = (
            select(WorkingSchedule)
            .where(WorkingSchedule.id == internal_id)
            .execution_options(populate_existing=True)
        )
        schedule = (await self.session.execute(stmt)).scalar_one()
        await self.session.refresh(schedule, attribute_names=["lines"])
        return schedule
