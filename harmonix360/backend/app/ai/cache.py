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

# Per-task-type TTLs in seconds.
#
# The keys are the HR/Payroll prompt families from Architecture §8.1; the
# AssetFlow ones (transfer_decision, booking_decision, asset_analysis) went with
# their domain in Phase 0 step 1. An unrecognised task_type falls back to
# "general", so a Phase 9 prompt family missing from this table still caches —
# it just doesn't get a tuned TTL.
TASK_TYPE_TTLS: dict[str, int] = {
    # A payslip is immutable once its payrun is validated, so its narration can
    # be cached hard. The deterministic rule engine — never the cache, and never
    # the AI — is what produced the numbers being narrated (Architecture §7/§10).
    "payslip_explanation": 3600,
    # Department variance and trends move only when a payrun does.
    "payroll_variance": 1800,
    # Anomaly narration sits on top of deterministic checks whose inputs change
    # as HR fixes the underlying records, so keep it short.
    "anomaly_narration": 300,
    # "Who is blocking payroll" / "summarize pending HR actions" answer a live
    # worklist; a stale answer there is actively misleading.
    "pending_actions": 120,
    "general": 600,             # 10 min default
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
