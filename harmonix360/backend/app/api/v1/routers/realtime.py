"""WebSocket endpoints for the realtime layer (Architecture §8.4).

    WS  /api/v1/ws/{channel}?token=<jwt>
    GET /api/v1/realtime/status

WHY THE TOKEN IS A QUERY PARAMETER
----------------------------------
The browser `WebSocket` constructor cannot set an `Authorization` header —
that is a limitation of the API, not a choice. The alternatives are a cookie
(which this product does not use, and which would need CSRF handling) or a
post-connect auth frame (which means accepting an unauthenticated socket first).
A short-lived bearer token in the query string is the standard compromise; the
socket is closed immediately if it does not verify, before it joins a channel.

The consequence worth writing down: query strings appear in access logs and
proxy logs more readily than headers do. In production this should be paired
with a short-lived, socket-scoped ticket rather than the session JWT. It is
recorded in the Phase 10 limitations rather than papered over.

CONNECTING IS NEVER REQUIRED
----------------------------
Every screen that uses this works without it. If the socket never opens, closes,
or drops a frame, a REST refetch produces the same answer — realtime here is an
enhancement over authoritative state, never a channel that carries state of its
own (Architecture §8.4). `/realtime/status` exists so the UI can SAY the live
feed is unavailable rather than silently looking stale.
"""
from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status

from app.api.v1.deps import CurrentUser, get_current_user
from app.core.security import decode_token
from app.models.enums import HR_ROLES, PAYROLL_ROLES, UserRole
from app.realtime.events import CHANNELS
from app.realtime.ws_manager import Subscriber, manager

router = APIRouter(tags=["Realtime"])


def _subscriber_from_token(token: str) -> Subscriber | None:
    """Build a principal from a signed token, or None if it does not verify.

    Mirrors `get_current_user`'s fail-closed behaviour on an unrecognised role
    claim: a token minted before the role vocabulary changed is read as the
    WEAKEST role, never as "no constraint". There is deliberately no
    unauthenticated-equals-demo-admin branch here — that convenience exists for
    curl against REST in non-production, and a socket that silently became an
    admin feed would be a much quieter mistake.
    """
    payload = decode_token(token or "")
    if not payload or "sub" not in payload:
        return None
    try:
        role = UserRole(payload.get("role", UserRole.EMPLOYEE.value))
    except ValueError:
        role = UserRole.EMPLOYEE
    return Subscriber(
        email=payload["sub"],
        role=role.value,
        employee_public_id=payload.get("employee_id"),
        is_hr=role in HR_ROLES or role is UserRole.ADMIN,
        is_payroll=role in PAYROLL_ROLES or role is UserRole.ADMIN,
    )


@router.websocket("/ws/{channel}")
async def realtime_channel(websocket: WebSocket, channel: str, token: str = Query(default="")):
    """Subscribe to one channel. Frames are events, never commands.

    Nothing the client sends is acted on. The receive loop exists only to
    notice a disconnect — a socket this server took instructions from would be
    a second write path into the application, which Architecture §9 forbids as
    firmly for a WebSocket as for anything else.
    """
    if channel not in CHANNELS:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Unknown channel")
        return

    subscriber = _subscriber_from_token(token)
    if subscriber is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Not authenticated")
        return

    # Coarse gate at connect time; `events._visible_to` then filters each frame
    # per subscriber. Both are needed: this one keeps a role that can never see
    # anything on a channel from holding a connection at all, and that one
    # keeps an Employee on a shared channel from seeing a colleague's row.
    if channel == "payroll" and not subscriber.is_payroll:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION, reason="Payroll role required"
        )
        return

    await manager.connect(channel, websocket, subscriber)
    try:
        await websocket.send_json(
            {
                "channel": channel,
                "event": "connected",
                "data": {"role": subscriber.role, "channel": channel},
            }
        )
        while True:
            # Read and discard. See the docstring: inbound frames are not
            # commands, and this await is how a disconnect is detected.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - a broken socket is not a server error
        pass
    finally:
        await manager.disconnect(channel, websocket)


@router.get("/realtime/status")
async def realtime_status(current_user: CurrentUser = Depends(get_current_user)):
    """What the live feed currently looks like from the server's side.

    Lets a client render "live" versus "reconnecting" honestly. The counts are
    this process's own connections — with more than one API replica this is a
    local view, which is fine for what it is used for and would be wrong to
    treat as a cluster-wide truth.
    """
    return {
        "channels": {channel: manager.channel_size(channel) for channel in CHANNELS},
        "total_connections": manager.total_connections(),
        "available": True,
        "note": (
            "Realtime is an enhancement over committed state. Every screen that uses it "
            "works correctly without it; REST remains authoritative."
        ),
    }
