"""Post-commit realtime events (Architecture §8.4).

    service stages an event  ->  transaction commits  ->  event is broadcast
                             ->  transaction rolls back  ->  event is DISCARDED

THE ORDERING IS STRUCTURAL, NOT A CONVENTION
--------------------------------------------
`queue_event` only ever appends to `session.info`. Nothing is sent from there.
Dispatch happens in exactly one place — `dispatch_after_commit`, called by
`get_db` immediately after `await session.commit()` returns, and by the worker
tasks that own their own sessions. If the commit raises, the generator's
`except` branch rolls back and dispatch is never reached, so the staged events
die with the transaction.

This is why it is not enough to "remember to broadcast after committing" in each
router. `get_db` commits AFTER the handler returns; a `broadcast()` written at
the end of a handler body runs BEFORE that commit, and would announce a
check-in that a later constraint violation then erased. Placing the only send
site after the only commit site makes the wrong order unwritable rather than
merely discouraged.

REALTIME IS NEVER LOAD-BEARING
------------------------------
Every failure below is swallowed and logged. A dead browser tab, a full send
buffer or a Redis hiccup must not turn a committed payroll transaction into an
error response — the socket is a notification of already-true state, and REST
refetch remains the authoritative read. That is what makes this P1/P2 rather
than P0 (Architecture §8.4).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.realtime.ws_manager import manager

logger = logging.getLogger("harmonix360.realtime.events")

#: The four channels Architecture §8.4 names, plus `payroll` carrying both
#: compute and bulk-email progress (both are "a long payroll job is moving",
#: and a client watching one wants the other).
CHANNEL_ATTENDANCE = "attendance"
CHANNEL_TIME_OFF = "time_off"
CHANNEL_APPROVALS = "approvals"
CHANNEL_PAYROLL = "payroll"

CHANNELS = (CHANNEL_ATTENDANCE, CHANNEL_TIME_OFF, CHANNEL_APPROVALS, CHANNEL_PAYROLL)

#: Where staged events live until the transaction commits.
_KEY = "realtime_events"


def queue_event(
    session: AsyncSession, channel: str, event: str, payload: Optional[dict] = None
) -> None:
    """Stage an event to be broadcast if — and only if — this transaction commits.

    Deliberately takes no `websocket`, no connection and no manager: a caller
    cannot accidentally send from here. It also never raises. A service is in
    the middle of a business transaction when it calls this, and an unknown
    channel name is not worth failing a leave approval over — it is logged and
    dropped.
    """
    if channel not in CHANNELS:  # pragma: no cover - guards a typo, not user input
        logger.warning("Unknown realtime channel %r; event %r dropped", channel, event)
        return
    session.info.setdefault(_KEY, []).append(
        {
            "channel": channel,
            "event": event,
            "at": datetime.now(UTC).isoformat(),
            "data": payload or {},
        }
    )


def staged_events(session: AsyncSession) -> list[dict]:
    """What is currently staged. Exposed for tests that assert nothing was sent
    before a commit, and for the rollback case where the list is discarded."""
    return list(session.info.get(_KEY, ()))


def discard_events(session: AsyncSession) -> None:
    session.info.pop(_KEY, None)


def _visible_to(subscriber, event: dict) -> bool:
    """Architecture §5, applied to a socket frame.

    A channel is not an audience. Without this, an Employee subscribed to
    `approvals` would receive every colleague's leave outcome — the same
    disclosure a REST endpoint would be faulted for, travelling over a
    WebSocket. The rules mirror the REST ones exactly:

      payroll     payroll roles only (HR Manager has no payroll access at all)
      attendance  HR roles, or the employee the event is about
      time_off    HR roles (the approver queue), or the subject
      approvals   the subject, or HR roles

    An unauthenticated socket (no principal) receives nothing. The route
    refuses those, so this is the second gate rather than the only one.
    """
    if subscriber is None:
        return False

    channel = event.get("channel")
    subject = (event.get("data") or {}).get("employee_id")

    if channel == CHANNEL_PAYROLL:
        return subscriber.is_payroll

    is_subject = bool(subject) and subscriber.employee_public_id == subject
    return subscriber.is_hr or subscriber.is_payroll or is_subject


async def dispatch_after_commit(session: AsyncSession) -> int:
    """Broadcast everything staged on `session`, then clear it.

    MUST be called only after a successful commit. Returns the number of
    successful sends, which is what the tests count — note that zero deliveries
    with connected clients is a delivery problem, while zero deliveries with no
    clients is the normal case, and neither is an error.
    """
    events = session.info.pop(_KEY, None)
    if not events:
        return 0

    delivered = 0
    for event in events:
        try:
            delivered += await manager.broadcast(
                event["channel"], event, visible_to=_visible_to
            )
        except Exception:  # noqa: BLE001 - realtime must never break a commit
            logger.warning(
                "Realtime dispatch failed for %s/%s", event["channel"], event["event"],
                exc_info=True,
            )
    return delivered


# --------------------------------------------------------------------------
# Typed helpers — one per event Architecture §8.4 names.
#
# These exist so the payload shape lives in one place rather than being spelled
# out at each call site. Every value is JSON-safe and every amount is a string
# (Architecture §10): a WebSocket frame is a JSON boundary like any other, and
# a float here would be a float on a payroll number.
# --------------------------------------------------------------------------


def _money(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def attendance_recorded(session: AsyncSession, attendance, *, action: str) -> None:
    """A check-in, check-out or correction — broadcast to the HR dashboard."""
    queue_event(
        session,
        CHANNEL_ATTENDANCE,
        f"attendance.{action}",
        {
            "attendance_id": attendance.public_id,
            "employee_id": attendance.employee.public_id,
            "employee_name": attendance.employee.full_name,
            "check_in": attendance.check_in.isoformat() if attendance.check_in else None,
            "check_out": attendance.check_out.isoformat() if attendance.check_out else None,
            "worked_hours": _money(attendance.worked_hours),
            "status": attendance.status.value,
        },
    )


def time_off_requested(session: AsyncSession, request) -> None:
    """A new request — broadcast to approvers."""
    queue_event(
        session,
        CHANNEL_TIME_OFF,
        "time_off.requested",
        {
            "request_id": request.public_id,
            "employee_id": request.employee.public_id,
            "employee_name": request.employee.full_name,
            "type": request.time_off_type.name,
            "affects_payroll": bool(request.time_off_type.payroll_integration),
            "date_from": request.date_from.isoformat(),
            "date_to": request.date_to.isoformat(),
            "duration": _money(request.duration),
            "status": request.status.value,
        },
    )


def time_off_decided(session: AsyncSession, request) -> None:
    """Approved or refused — broadcast to the requesting employee."""
    queue_event(
        session,
        CHANNEL_APPROVALS,
        "time_off.decided",
        {
            "request_id": request.public_id,
            "employee_id": request.employee.public_id,
            "employee_name": request.employee.full_name,
            "status": request.status.value,
            "decision_note": request.decision_note,
            "duration": _money(request.duration),
        },
    )


def payroll_progress(
    session: AsyncSession,
    payrun,
    *,
    stage: str,
    computed: Optional[int] = None,
    skipped: Optional[int] = None,
    blocking: Optional[dict] = None,
) -> None:
    """Payroll compute progress — broadcast to the initiating payroll session.

    `stage` is the transition that just committed (`computed`, `validated`,
    `paid`), never one that is about to be attempted. A progress event that ran
    ahead of its transaction would show a payrun as validated while the
    validation was still deciding.
    """
    queue_event(
        session,
        CHANNEL_PAYROLL,
        f"payrun.{stage}",
        {
            "payrun_id": payrun.public_id,
            "name": payrun.name,
            "status": payrun.status.value,
            "period_start": payrun.period_start.isoformat(),
            "period_end": payrun.period_end.isoformat(),
            "computed_count": computed,
            "skipped_count": skipped,
            "blocking_issues": blocking or {},
        },
    )


def delivery_progress(
    session: AsyncSession,
    payrun_public_id: str,
    *,
    stage: str,
    queued: Optional[int] = None,
    sent: Optional[int] = None,
    failed: Optional[int] = None,
) -> None:
    """Bulk-email progress. `stage` is `queued`, `progress` or `finished`."""
    queue_event(
        session,
        CHANNEL_PAYROLL,
        f"payslip_delivery.{stage}",
        {
            "payrun_id": payrun_public_id,
            "queued": queued,
            "sent": sent,
            "failed": failed,
        },
    )


__all__ = [
    "CHANNELS",
    "CHANNEL_APPROVALS",
    "CHANNEL_ATTENDANCE",
    "CHANNEL_PAYROLL",
    "CHANNEL_TIME_OFF",
    "attendance_recorded",
    "delivery_progress",
    "discard_events",
    "dispatch_after_commit",
    "payroll_progress",
    "queue_event",
    "staged_events",
    "time_off_decided",
    "time_off_requested",
]
