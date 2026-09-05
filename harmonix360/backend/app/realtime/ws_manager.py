"""Realtime presentation layer — native FastAPI WebSockets.

STATUS: DORMANT UNTIL PHASE 10.
--------------------------------
Architecture §8.4 lists this module under "Adapted", with `n/a` as its original
target — it never existed in the platform foundation, so there was nothing to
delete here and nothing to retarget. What ships now is the connection registry
the Phase 10 channels will share, with no producer wired to it and no
`/ws` route mounted in `app/main.py`. Importing this module has no effect on a
running app.

THE RULE THAT MAKES THIS SAFE TO ADD LATER (Architecture §8.4): a broadcast is
a notification of already-committed state, never a store of truth, and it fires
strictly AFTER the transaction commits. If the socket layer is down, a REST
refetch still produces the correct answer — which is exactly why realtime is
P1/P2 and never P0. Nothing here may write to the database, and no business
rule may depend on a broadcast having been delivered.

Phase 10 channels:
    attendance      check-ins, broadcast to the HR dashboard
    time_off        new requests, broadcast to approvers
    approvals       outcomes, broadcast to the requesting employee
    payroll         compute and bulk-email progress, broadcast to the
                    initiating Payroll user's session only
"""
import asyncio
import logging
from collections import defaultdict
from typing import Any, Dict, Set

logger = logging.getLogger("harmonix360.realtime")


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
        self._lock = asyncio.Lock()

    async def connect(self, channel: str, websocket: Any) -> None:
        await websocket.accept()
        async with self._lock:
            self._channels[channel].add(websocket)
        logger.debug("ws connected: channel=%s size=%d", channel, len(self._channels[channel]))

    async def disconnect(self, channel: str, websocket: Any) -> None:
        async with self._lock:
            self._channels[channel].discard(websocket)
            if not self._channels[channel]:
                self._channels.pop(channel, None)

    async def broadcast(self, channel: str, payload: dict) -> int:
        """Send `payload` to every socket on `channel`. Returns the delivered count.

        A send failure drops that one socket and is not re-raised: the caller is
        a post-commit hook, and a dead browser tab must never turn a committed
        payroll transaction into an error response.
        """
        async with self._lock:
            targets = list(self._channels.get(channel, ()))

        delivered = 0
        for websocket in targets:
            try:
                await websocket.send_json(payload)
                delivered += 1
            except Exception:
                logger.debug("ws send failed on channel=%s; dropping socket", channel, exc_info=True)
                await self.disconnect(channel, websocket)
        return delivered

    def channel_size(self, channel: str) -> int:
        return len(self._channels.get(channel, ()))


manager = ConnectionManager()
