"""
Taskiq task wrapping provider_router.generate().

This is the correct path for router-triggered AI calls:
Router -> run_ai_generate.kiq() -> worker calls generate() -> result in backend.

The AIDecisionNode does NOT use this task — it calls generate() directly
since it's already inside a worker task (avoids deadlock risk).
"""
import logging
from app.jobs.broker import broker
from app.ai.provider_router import generate, AIUnavailableError

logger = logging.getLogger("harmonix360.jobs.ai")


@broker.task(task_name="run_ai_generate")
async def run_ai_generate(prompt: str, context: dict, task_type: str) -> dict:
    """
    Execute an AI generation call inside the Taskiq worker.

    Returns the AIResponse as a dict (serializable for the result backend).
    On AIUnavailableError, returns a structured error dict rather than crashing.
    """
    try:
        response = await generate(prompt=prompt, context=context, task_type=task_type)
        return {
            "status": "completed",
            "result": response.model_dump(),
        }
    except AIUnavailableError as e:
        logger.warning("AI unavailable in task: %s", e)
        return {
            "status": "ai_unavailable",
            "result": None,
            "error": str(e),
        }
    except Exception as e:
        logger.exception("Unexpected error in AI task")
        return {
            "status": "failed",
            "result": None,
            "error": str(e),
        }
