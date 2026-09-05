"""Realtime presentation layer — native FastAPI WebSockets.

STATUS: LIVE AS OF PHASE 10.
----------------------------
Architecture §8.4. What ships here is the connection registry plus the
per-subscriber visibility rule; the events themselves are staged and dispatched
by `app/realtime/events.py`, which is the only module that calls `broadcast`.

THE RULE THAT MAKES THIS SAFE (Architecture §8.4): a broadcast is a
notification of already-committed state, never a store of truth, and it fires
strictly AFTER the transaction commits. If the socket layer is down, a REST
refetch still produces the correct answer — which is exactly why realtime is
P1/P2 and never P0. Nothing here writes to the database, and no business rule
may depend on a broadcast having been delivered.

WHY A SUBSCRIBER IS MORE THAN A SOCKET
--------------------------------------
A channel is not an audience. Architecture §5's first row gives an Employee
access to their OWN records and nothing else, and a plain per-channel fan-out
would hand every subscriber on `approvals` every colleague's leave outcome —
an RBAC bypass that happens to travel over a WebSocket instead of a GET. So a
connection carries the principal that opened it, and `broadcast` consults
`visible_to` per subscriber. The socket is held to the same matrix the REST API
is, because it is the same data.

Channels (Architecture §8.4):
    attendance      check-ins, broadcast to the HR dashboard
    time_off        new requests, broadcast to approvers
    approvals       outcomes, broadcast to the requesting employee
    payroll         compute and bulk-email progress, to payroll roles
"""
import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Set

logger = logging.getLogger("harmonix360.realtime")


@dataclass(frozen=True)
class Subscriber:
    """Who is on the other end of a socket.

    `employee_public_id` is the anchor for every "…but only their own" rule,
    exactly as it is on `CurrentUser`. It comes from the signed token, never
    from anything the client sent over the socket.
    """

    email: str
    role: str
    employee_public_id: Optional[str] = None
    is_hr: bool = False
    is_payroll: bool = False


class ConnectionManager:
    """Tracks live WebSocket connections per channel.

    One lock guards the registry because `broadcast` iterates the socket set
    while a concurrent connect/disconnect may be mutating it — a plain
    `for ws in self._channels[channel]` over a set another task is editing
    raises RuntimeError mid-broadcast. Iterating a snapshot taken under the
    lock keeps a slow or dead client from blocking the registry for everyone.
    """

    def __init__(self) -> None:
        self._channels: Dict[str, Set[Any]] = defaultdict(set)
        self._principals: Dict[Any, Subscriber] = {}
        self._lock = asyncio.Lock()

    async def connect(
        self, channel: str, websocket: Any, principal: Optional[Subscriber] = None
    ) -> None:
        await websocket.accept()
        async with self._lock:
            self._channels[channel].add(websocket)
            if principal is not None:
                self._principals[websocket] = principal
        logger.debug("ws connected: channel=%s size=%d", channel, len(self._channels[channel]))

    async def disconnect(self, channel: str, websocket: Any) -> None:
        async with self._lock:
            self._channels[channel].discard(websocket)
            self._principals.pop(websocket, None)
            if not self._channels[channel]:
                self._channels.pop(channel, None)

    async def broadcast(
        self,
        channel: str,
        payload: dict,
        *,
        visible_to: Optional[Callable[[Optional[Subscriber], dict], bool]] = None,
    ) -> int:
        """Send `payload` to every socket on `channel`. Returns the delivered count.

        `visible_to` is applied per subscriber. A subscriber the predicate
        rejects is skipped silently — it is not an error for an event to be
        none of your business.

        A send failure drops that one socket and is not re-raised: the caller is
        a post-commit hook, and a dead browser tab must never turn a committed
        payroll transaction into an error response.
        """
        async with self._lock:
            targets = [(ws, self._principals.get(ws)) for ws in self._channels.get(channel, ())]

        delivered = 0
        for websocket, principal in targets:
            if visible_to is not None and not visible_to(principal, payload):
                continue
            try:
                await websocket.send_json(payload)
                delivered += 1
            except Exception:
                logger.debug("ws send failed on channel=%s; dropping socket", channel, exc_info=True)
                await self.disconnect(channel, websocket)
        return delivered

    def channel_size(self, channel: str) -> int:
        return len(self._channels.get(channel, ()))

    def total_connections(self) -> int:
        return sum(len(sockets) for sockets in self._channels.values())


manager = ConnectionManager()
