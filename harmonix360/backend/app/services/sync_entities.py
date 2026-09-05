"""Registers the entity types this PeoplePay360 deployment exposes through
/sync/pull and /sync/push. Imported for its registration side effect only
(app/services/sync.py does `from app.services import sync_entities  # noqa: F401`).

Registers exactly TWO entities, per Architecture §8.3, and both CREATE-only:

    attendance          check-in / check-out CREATION. Attendance CORRECTIONS
                        stay online-only and role-gated: an offline device may
                        record that someone arrived, never rewrite what was
                        already recorded.
    time_off_request    creation only, never approval or refusal.

Nothing else is ever registered. Payroll, Salary Rule, Contract and every other
core entity are deliberately excluded: offline capability is opt-in by registry
membership, and payroll mutations are never opted in.

WHY THESE REGISTRATIONS ARE NOT ONE LINE EACH
---------------------------------------------
The module this replaces predicted that "registering an entity is one
`register_syncable_entity(...)` call in this file and nothing else". That was
true of AssetFlow's `note` and `asset` — entities with no domain rules, whose
rows the engine could safely build straight from a client payload. It is not
true of these two, and finding out why is the substance of this phase.

The generic engine, left to itself, would have:

1. **Written rows without any authorization.** `_apply_create` built
   `model(**payload)` and called `BaseService.create`. `AttendanceService.
   create_attendance` calls `employee_for(...)`, which is what enforces
   Architecture §5's "Attendance (own)" — an Employee may create their OWN
   attendance and nobody else's. Through the generic path an Employee could
   have posted attendance for anyone in the company.
2. **Written rows without any derivation.** `worked_hours` and `status` are
   server-computed (`AttendanceService._compute`); leave `duration` is computed
   by `request_duration`. The generic path would have persisted whatever the
   client sent, or NULL.
3. **Served every row to everyone.** `SyncService.pull` filters by tenant and
   cursor. With no per-entity scoping, any authenticated login pulling
   `attendance` would have received the whole organisation's attendance.
4. **Accepted UPDATE and DELETE.** An attendance correction is an UPDATE.
   `AttendanceService.correct` guards it with `require_hr`; the generic UPDATE
   path does not, so the correction gate would have been bypassed by choosing
   a different verb.

Each of those is a *second, looser path to the database*, which Architecture §9
forbids in as many words. So `SyncableEntity` gained three optional fields —
`allowed_ops`, `create_handler`, `scope_filter` — each defaulting to the old
behaviour so no other registration changes meaning. The sync engine's cursor,
savepoint-per-mutation, `sync_mutations` idempotency, conflict envelope, audit
write and both HTTP routes are untouched. What changed is that a registration
can now state the rules its entity already has.

IDEMPOTENCY
-----------
Unchanged, and it is the mechanism PRD §7 measures ("zero duplicate records
across a kill-network → mutate → reconnect cycle"). `SyncService.push` looks up
`(actor_key, client_mutation_id)` in `sync_mutations` before applying anything
and replays the stored result verbatim on a repeat. The client generates
`client_mutation_id` once, when the mutation is queued — not when it is sent —
so every retry of the same queued mutation carries the same id.

CONFLICTS
---------
See `docs/offline-sync-conflicts.md`. In short: both registered operations are
creates, and the version-conflict path only runs for UPDATE and DELETE, so it
is unreachable for these two entities by construction rather than by luck.
"""

from typing import Any, Dict, List

from sqlalchemy import false

from app.models.attendance import Attendance
from app.models.time_off import TimeOffRequest
from app.repositories.hr import EmployeeRepository
from app.schemas.attendance import AttendanceCreate
from app.schemas.time_off import RequestCreate
from app.services.attendance import AttendanceService
from app.services.sync_registry import SyncableEntity, register_syncable_entity
from app.services.time_off import TimeOffRequestService

CREATE_ONLY = frozenset({"CREATE"})


# --------------------------------------------------------------------- scope


def _scoped_to(model):
    """Architecture §5's first row, as SQL, bound to one model's `employee_id`.

    HR and above see every row — they already may, through `/attendance/` and
    `/time-off-requests/`. An Employee sees only rows whose `employee_id` is
    their own, resolved from the **signed** `employee_id` claim rather than
    from anything the client sent.

    A login with neither an HR role nor an Employee row gets `false()`: no
    rows, rather than all rows. That case should not arise, since the only
    non-HR role here is Employee — but the direction a scoping bug fails in
    matters more than how likely it is.
    """

    async def scope(session, user) -> List[Any]:
        if user.is_hr():
            return []
        public_id = getattr(user, "employee_public_id", None)
        if not public_id:
            return [false()]
        employee = await EmployeeRepository(session).get_by_public_id(public_id)
        if employee is None:
            return [false()]
        return [model.employee_id == employee.id]

    return scope


# ------------------------------------------------------------------ handlers


async def _create_attendance(session, user, payload: Dict[str, Any]):
    """PS B3's check-in/check-out, arriving from a device that was offline.

    Goes through `AttendanceService.create_attendance`, the same method
    `POST /attendance/` calls: `employee_for` scoping, `_compute`'s worked-hours
    and status derivation, and the `CREATE_ATTENDANCE` audit row.

    The timestamps come from the PAYLOAD, not from the server clock. That is
    the whole point of syncing attendance: the check-in happened when the
    person arrived, not when their phone found a signal again. `check_out` is
    optional, so a device can push a shift that is still open and close it
    online later.

    The audit row says `CREATE_ATTENDANCE`, exactly as an online check-in
    does, because it IS the same operation through the same service. What
    distinguishes the two is the `sync_mutations` row the engine writes
    alongside it, which carries the client mutation id.
    """
    dto = AttendanceCreate(**payload)
    return await AttendanceService(session).create_attendance(dto, user)


async def _create_time_off_request(session, user, payload: Dict[str, Any]):
    """PS B4's request submission, from a device that was offline.

    Goes through `TimeOffRequestService.create_request`: `employee_for`
    scoping, `request_duration` (which is the number a balance is later
    debited by, so a second implementation of it would eventually disagree),
    the type's `requires_approval` / `requires_allocation` policy, and the
    auto-approval path for types that skip approval.

    Note what that last clause means: a request for a `requires_approval=False`
    type auto-approves on arrival and debits an allocation, inside the same
    transaction, at the moment the device reconnects. That is the same
    behaviour as submitting it online, and it is the reason APPROVAL itself is
    not syncable — approving is a decision about a live balance, and a device
    holding a stale copy of that balance must not be allowed to make it.
    """
    dto = RequestCreate(**payload)
    return await TimeOffRequestService(session).create_request(dto, user)


# ------------------------------------------------------------- serialization


def _attendance_json(row: Attendance) -> Dict[str, Any]:
    """What a device needs to render its own attendance list offline.

    Public ids only, never integer primary keys, and `Decimal` stringified —
    the same rule every other JSON boundary in this codebase follows
    (Architecture §6/§10).
    """
    return {
        "id": row.public_id,
        "employee_id": row.employee.public_id if row.employee else None,
        "check_in": row.check_in.isoformat() if row.check_in else None,
        "check_out": row.check_out.isoformat() if row.check_out else None,
        "worked_hours": str(row.worked_hours) if row.worked_hours is not None else None,
        "status": row.status.value if row.status else None,
    }


def _time_off_request_json(row: TimeOffRequest) -> Dict[str, Any]:
    return {
        "id": row.public_id,
        "employee_id": row.employee.public_id if row.employee else None,
        "time_off_type_id": row.time_off_type.public_id if row.time_off_type else None,
        "date_from": row.date_from.isoformat() if row.date_from else None,
        "date_to": row.date_to.isoformat() if row.date_to else None,
        "duration": str(row.duration) if row.duration is not None else None,
        "status": row.status.value if row.status else None,
        "reason": row.reason,
    }


# ----------------------------------------------------------- registrations


register_syncable_entity(
    SyncableEntity(
        entity_type="attendance",
        model=Attendance,
        service_factory=AttendanceService,
        create_schema=AttendanceCreate,
        # Never reached: `allowed_ops` is CREATE-only, so the engine rejects an
        # UPDATE before it looks for an update schema. Named honestly rather
        # than left as a plausible-looking correction schema that nothing uses.
        update_schema=AttendanceCreate,
        serialize=_attendance_json,
        create_action="SYNC_CREATE_ATTENDANCE",
        update_action="SYNC_UPDATE_ATTENDANCE",
        delete_action="SYNC_DELETE_ATTENDANCE",
        allowed_ops=CREATE_ONLY,
        create_handler=_create_attendance,
        scope_filter=_scoped_to(Attendance),
    )
)

register_syncable_entity(
    SyncableEntity(
        entity_type="time_off_request",
        model=TimeOffRequest,
        service_factory=TimeOffRequestService,
        create_schema=RequestCreate,
        update_schema=RequestCreate,  # never reached; see above
        serialize=_time_off_request_json,
        create_action="SYNC_CREATE_TIME_OFF_REQUEST",
        update_action="SYNC_UPDATE_TIME_OFF_REQUEST",
        delete_action="SYNC_DELETE_TIME_OFF_REQUEST",
        allowed_ops=CREATE_ONLY,
        create_handler=_create_time_off_request,
        scope_filter=_scoped_to(TimeOffRequest),
    )
)
