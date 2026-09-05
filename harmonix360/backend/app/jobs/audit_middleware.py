"""
Generic Taskiq middleware that auto-writes an audit_log entry around every job execution.

Architecture ref: Section 5 point 3 — "Use a Taskiq middleware to auto-write the
audit log entry around every AI job, rather than repeating that call inside each task."

This is implemented once, generically, not per-task.
"""
import uuid
import logging
import time
from typing import Any
from taskiq import TaskiqMiddleware, TaskiqMessage, TaskiqResult
from app.core.database import AsyncSessionLocal
from app.models.entities import AuditLog

logger = logging.getLogger("harmonix360.jobs.audit")


class JobAuditMiddleware(TaskiqMiddleware):
    """Wraps every Taskiq job with an audit log entry."""

    async def pre_execute(self, message: TaskiqMessage) -> TaskiqMessage:
        """Record start time before task execution."""
        message.labels["_audit_start_time"] = str(time.time())
        return message

    async def post_execute(
        self, message: TaskiqMessage, result: TaskiqResult[Any]
    ) -> None:
        """Write an audit_log row after every job completes (success or failure)."""
        try:
            start_time = float(message.labels.get("_audit_start_time", "0"))
            duration_ms = int((time.time() - start_time) * 1000)

            after_diff = {
                "task_name": message.task_name,
                "duration_ms": duration_ms,
                "is_error": result.is_err,
            }

            if result.is_err:
                after_diff["error"] = str(result.error) if result.error else "Unknown error"
                action = f"JOB_FAILED_{message.task_name}"
            else:
                action = f"JOB_COMPLETED_{message.task_name}"

            async with AsyncSessionLocal() as session:
                audit_entry = AuditLog(
                    public_id=f"aud_{uuid.uuid4().hex[:12]}",
                    actor="system:taskiq",
                    action=action,
                    entity="Job",
                    entity_id=message.task_id,
                    after_diff=after_diff,
                )
                session.add(audit_entry)
                await session.commit()

            logger.info(
                "Audit logged job %s (task=%s, duration=%dms, error=%s)",
                message.task_id,
                message.task_name,
                duration_ms,
                result.is_err,
            )
        except Exception:
            # Never let audit logging crash the worker
            logger.exception("Failed to write audit log for job %s", message.task_id)
