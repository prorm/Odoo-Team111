"""Generic 409 conflict, raised by BaseRepository.update/soft_delete (and the
sync push path) whenever an entity's version does not match what the caller
expected. One shape, reused everywhere a version conflict can occur — see
HARMONIX360_ARCHITECTURE.md §13.3.
"""
from typing import Any, Dict, Optional
from fastapi import Request
from fastapi.responses import JSONResponse


class ConflictError(Exception):
    def __init__(
        self,
        entity_type: str,
        entity_id: str,
        current_version: int,
        current_state: Dict[str, Any],
        known_version: Optional[int] = None,
        message: str = "Entity has been modified since your last known version.",
    ):
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.current_version = current_version
        self.current_state = current_state
        self.known_version = known_version
        self.message = message
        super().__init__(message)

    def to_envelope(self) -> Dict[str, Any]:
        return {
            "error": {
                "code": "VERSION_CONFLICT",
                "message": self.message,
                "details": {
                    "entity_type": self.entity_type,
                    "entity_id": self.entity_id,
                    "known_version": self.known_version,
                    "current_version": self.current_version,
                    "current_state": self.current_state,
                },
            }
        }


async def conflict_error_handler(request: Request, exc: ConflictError) -> JSONResponse:
    return JSONResponse(status_code=409, content=exc.to_envelope())
