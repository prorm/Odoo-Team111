import time
from fastapi import HTTPException, status, Request
from app.core.redis import redis_client

class RateLimiter:
    def __init__(self, requests_per_minute: int = 60):
        self.requests_per_minute = requests_per_minute

    async def __call__(self, request: Request):
        client_ip = request.client.host if request.client else "127.0.0.1"
        key = f"rate_limit:{client_ip}:{request.url.path}"
        current_minute = int(time.time() // 60)
        bucket_key = f"{key}:{current_minute}"

        pipe = redis_client.pipeline()
        pipe.incr(bucket_key)
        pipe.expire(bucket_key, 60)
        results = await pipe.execute()
        
        request_count = results[0]
        if request_count > self.requests_per_minute:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Try again later."
            )

rate_limiter = RateLimiter(requests_per_minute=60)
