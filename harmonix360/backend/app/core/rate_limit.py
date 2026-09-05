"""Per-client, per-path request throttling.

A fixed-window counter in Redis: one key per (client IP, path, minute), which
is cheap and good enough for the abuse this is here to blunt. It is not a
precise limiter — a client can send a full window's worth at 59.9s and again at
60.1s — and it is deliberately not trying to be. A sliding-window or token
bucket costs more Redis round-trips per request than the protection is worth at
this scale.

The limit is a setting rather than a constant because the right number depends
on the screen: opening the Employee form fires the record, its smart-button
counts and a manager lookup, so a limit tuned for "one request per user action"
throttles ordinary use. It is also what lets the test suite raise the ceiling
instead of pretending the limiter isn't there.
"""
import logging
import time

from fastapi import HTTPException, Request, status

from app.core.config import settings
from app.core.redis import redis_client

logger = logging.getLogger("harmonix360.rate_limit")


class RateLimiter:
    def __init__(self, requests_per_minute: int | None = None):
        self._configured = requests_per_minute

    @property
    def requests_per_minute(self) -> int:
        # Read per-request rather than captured at construction: the module is
        # imported at app start, and reading it lazily means a changed setting
        # takes effect without rebuilding every router's dependency.
        return self._configured or settings.RATE_LIMIT_PER_MINUTE

    async def __call__(self, request: Request):
        client_ip = request.client.host if request.client else "127.0.0.1"
        bucket_key = f"rate_limit:{client_ip}:{request.url.path}:{int(time.time() // 60)}"

        try:
            pipe = redis_client.pipeline()
            pipe.incr(bucket_key)
            pipe.expire(bucket_key, 60)
            request_count = (await pipe.execute())[0]
        except Exception:
            # Fail OPEN, not closed. Redis being unavailable is an operational
            # problem; turning it into "nobody can run payroll" makes an
            # outage strictly worse. The limiter blunts abuse — it is not an
            # authorization control, and nothing downstream relies on it.
            logger.warning("rate limiter unavailable; allowing request", exc_info=True)
            return

        if request_count > self.requests_per_minute:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Try again later.",
                headers={"Retry-After": "60"},
            )


rate_limiter = RateLimiter()
