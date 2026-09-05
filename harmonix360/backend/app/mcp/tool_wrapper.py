"""
Shared MCP tool plumbing.

Every hand-written tool in app/mcp/server.py repeated the same shape: validate
the MCP agent API key, open a DB session, run entity-specific logic, commit on
success / rollback on failure, and return a uniform {"status": "error", ...}
envelope on any exception. @mcp_tool(mcp) extracts all of that so a new tool
is just its entity-specific body plus one decorator.
"""
import inspect
import logging
import secrets
from typing import Any, Callable
from app.core.config import settings
from app.core.database import AsyncSessionLocal

logger = logging.getLogger("harmonix360.mcp")

EXPECTED_API_KEY = settings.MCP_AGENT_API_KEY


def _validate_api_key(api_key: str | None) -> None:
    """Validate MCP agent API key — separate from user JWT auth.

    compare_digest, not ==: `==` on strings short-circuits at the first
    differing byte, so how long the rejection takes leaks how many leading
    characters were right, and a caller who can time enough attempts can
    recover the key one character at a time. compare_digest takes the same time
    regardless of where the mismatch is.
    """
    if not api_key or not secrets.compare_digest(api_key, EXPECTED_API_KEY):
        raise ValueError("Invalid or missing MCP agent API key")


def _public_signature(fn: Callable) -> inspect.Signature:
    """fn's signature with its leading `session` parameter dropped — that's
    what the MCP schema/client should see, not our internal wrapper param."""
    sig = inspect.signature(fn)
    params = list(sig.parameters.values())[1:]
    return sig.replace(parameters=params)


def _public_annotations(fn: Callable) -> dict:
    """fn.__annotations__ minus `session` — FastMCP/pydantic build the tool's
    JSON schema from typing.get_type_hints(), which reads __annotations__
    directly rather than following __signature__."""
    return {k: v for k, v in fn.__annotations__.items() if k != "session"}


def mcp_tool(mcp_instance) -> Callable:
    """
    Decorator + registrar. The decorated function's first parameter must be
    `session` (an AsyncSession, injected — never exposed to the MCP client);
    every other parameter becomes part of the tool's public schema exactly as
    declared, including `api_key`.

        @mcp_tool(mcp)
        async def my_tool(session, thing_id: str, api_key: str = "") -> dict:
            '''Docstring shown to the LLM/MCP client.'''
            ...entity-specific logic only...
    """
    def decorator(fn: Callable) -> Callable:
        async def wrapper(**kwargs) -> Any:
            api_key = kwargs.get("api_key", "")
            try:
                _validate_api_key(api_key)
            except Exception as e:
                return {"status": "error", "error": str(e)}

            async with AsyncSessionLocal() as session:
                try:
                    result = await fn(session, **kwargs)
                    await session.commit()
                    return result
                except Exception as e:
                    await session.rollback()
                    logger.exception("MCP tool '%s' failed", fn.__name__)
                    return {"status": "error", "error": str(e)}

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        wrapper.__signature__ = _public_signature(fn)
        wrapper.__annotations__ = _public_annotations(fn)
        return mcp_instance.tool()(wrapper)

    return decorator
