"""
Taskiq broker configuration with Redis backend.

Architecture ref: Section 1 (app/jobs/broker.py), Section 5 point 3.
Broker + result backend both use Redis. The audit middleware is registered
here so it wraps every task execution generically.
"""
import logging
from taskiq_redis import ListQueueBroker, RedisAsyncResultBackend
from app.core.config import settings

logger = logging.getLogger("harmonix360.jobs")

# Result backend stores task results in Redis for polling via GET /api/v1/ai/jobs/{id}
result_backend = RedisAsyncResultBackend(
    redis_url=settings.REDIS_URL,
    result_ex_time=3600,  # results expire after 1 hour
)

# Broker: ListQueueBroker is async-native and uses Redis LIST for queueing
broker = ListQueueBroker(
    url=settings.REDIS_URL,
).with_result_backend(result_backend)

# Import and register audit middleware after broker is created
from app.jobs.audit_middleware import JobAuditMiddleware  # noqa: E402
from app.core.telemetry import setup_telemetry  # noqa: E402

setup_telemetry()
broker.add_middlewares([JobAuditMiddleware()])

logger.info("Taskiq broker configured with Redis at %s", settings.REDIS_URL)
