"""Replay the response of a request that was already served under the same
`Idempotency-Key` (Architecture §6).

The key is REQUIRED on Payrun create and compute — enforced per-route by
`app.api.v1.deps.require_idempotency_key`, because this middleware is
deliberately permissive about a request that omits one and something has to
say "not optional here". This is the half of the duplicate-payslip defence
that catches the double-click before it reaches the database; the other half
is `uq_payslip_payrun_employee`, which catches it if it gets there anyway
(PRD §9's risk register).

SCOPE OF THE CACHE KEY
----------------------
The stored key is `idempotency:{METHOD}:{path}:{key}`, not `idempotency:{key}`.
The middleware is still entity-blind — it inspects no body and knows no model —
but a key is only a promise about repeating THE SAME request. A client that
generates one key per user action and sends it with both "create payrun" and
then "compute payrun" (an easy thing to do, and the exact shape of PS B5's
wizard finishing straight into PS B6's first action) would otherwise get the
create's 201 body back as the answer to Compute: no compute would run, and the
screen would show a freshly created payrun as though it had been computed.
Including the method and path costs nothing and makes that impossible.
"""
import json

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.redis import redis_client

#: A day. Long enough to cover any retry a human or a client library will
#: make, short enough that a key reused next week is not answered from a
#: response computed against last week's data.
CACHE_TTL_SECONDS = 86400


def cache_key(method: str, path: str, idempotency_key: str) -> str:
    """Exposed so tests and debugging can name the exact entry a request
    produces without duplicating the format string."""
    return f"idempotency:{method}:{path}:{idempotency_key}"


class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method not in ("POST", "PUT", "PATCH"):
            return await call_next(request)

        idempotency_key = request.headers.get("Idempotency-Key")
        if not idempotency_key:
            return await call_next(request)

        redis_key = cache_key(request.method, request.url.path, idempotency_key)
        cached_response = await redis_client.get(redis_key)
        if cached_response:
            data = json.loads(cached_response)
            return Response(
                content=data["content"],
                status_code=data["status_code"],
                headers=data.get("headers", {}),
                media_type="application/json",
            )

        response = await call_next(request)

        # Only successes are cached. A 409 from a duplicate compute, or a 400
        # from a bad body, must be re-evaluated on retry: the caller may have
        # fixed the very thing that failed, and replaying the failure would
        # make the fix look ineffective.
        if 200 <= response.status_code < 300:
            response_body = [chunk async for chunk in response.body_iterator]
            body_bytes = b"".join(response_body)

            cache_payload = {
                "status_code": response.status_code,
                "content": body_bytes.decode("utf-8"),
                "headers": {"X-Cache-Lookup": "HIT"},
            }
            await redis_client.set(redis_key, json.dumps(cache_payload), ex=CACHE_TTL_SECONDS)

            return Response(
                content=body_bytes,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        return response
