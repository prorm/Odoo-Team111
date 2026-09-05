"""
Redis-backed AI response cache.

Architecture ref: Section 5 point 4 — "Before calling any provider, check Redis cache:
key = hash of (prompt, context, task_type). Cache hits skip the provider entirely.
TTL configurable per task type."
"""
import hashlib
import json
import logging
from typing import Optional
from app.core.redis import redis_client
from app.core.config import settings

logger = logging.getLogger("harmonix360.ai.cache")

# Per-task-type TTLs in seconds
TASK_TYPE_TTLS: dict[str, int] = {
    "transfer_decision": 300,   # 5 min — decisions are context-sensitive
    "booking_decision": 300,
    "general": 600,             # 10 min default
    "asset_analysis": 600,
}


def _cache_key(prompt: str, context: dict, task_type: str) -> str:
    """Generate a deterministic cache key from the input parameters."""
    payload = json.dumps({"prompt": prompt, "context": context, "task_type": task_type}, sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    return f"ai_cache:{digest}"


class AIResponseCache:
    """Redis-backed cache for AI provider responses."""

    @staticmethod
    async def get(prompt: str, context: dict, task_type: str) -> Optional[dict]:
        """Check cache for a previous AI response. Returns parsed dict or None."""
        key = _cache_key(prompt, context, task_type)
        cached = await redis_client.get(key)
        if cached:
            logger.info("AI cache HIT for task_type=%s key=%s", task_type, key[:32])
            return json.loads(cached)
        logger.debug("AI cache MISS for task_type=%s", task_type)
        return None

    @staticmethod
    async def set(prompt: str, context: dict, task_type: str, response_data: dict) -> None:
        """Store an AI response in cache with task-type-specific TTL."""
        key = _cache_key(prompt, context, task_type)
        ttl = TASK_TYPE_TTLS.get(task_type, settings.AI_CACHE_DEFAULT_TTL)
        await redis_client.setex(key, ttl, json.dumps(response_data))
        logger.info("AI cache SET for task_type=%s ttl=%ds", task_type, ttl)
