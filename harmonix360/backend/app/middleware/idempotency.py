import json
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from app.core.redis import redis_client

class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method not in ("POST", "PUT", "PATCH"):
            return await call_next(request)

        idempotency_key = request.headers.get("Idempotency-Key")
        if not idempotency_key:
            return await call_next(request)

        redis_key = f"idempotency:{idempotency_key}"
        cached_response = await redis_client.get(redis_key)
        if cached_response:
            data = json.loads(cached_response)
            return Response(
                content=data["content"],
                status_code=data["status_code"],
                headers=data.get("headers", {}),
                media_type="application/json"
            )

        response = await call_next(request)

        if 200 <= response.status_code < 300:
            response_body = [chunk async for chunk in response.body_iterator]
            body_bytes = b"".join(response_body)
            
            cache_payload = {
                "status_code": response.status_code,
                "content": body_bytes.decode("utf-8"),
                "headers": {"X-Cache-Lookup": "HIT"}
            }
            await redis_client.setex(redis_key, 86400, json.dumps(cache_payload))
            
            return Response(
                content=body_bytes,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type
            )

        return response
