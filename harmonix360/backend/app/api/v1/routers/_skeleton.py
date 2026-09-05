"""Shared list-endpoint helper for the Phase 0 domain skeleton.

Every HR router needs the same opening move: an authenticated, role-gated,
paginated list. Writing it fourteen times would make the ONE thing that differs
per router — which roles Architecture §5 lets through — the part hardest to
read, buried in identical boilerplate.

`build_list_router` therefore takes the role set as its most prominent argument
so a reviewer can check a router against the permission matrix at a glance,
without reading any FastAPI plumbing.

This helper is scaffolding, and it should shrink as the phases land: as each
entity grows real endpoints, its router stops calling this and spells its own
routes out. Nothing here is meant to survive as a generic CRUD framework —
Architecture §2 removed the last one of those on purpose.
"""
from typing import Callable, Iterable, Type

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import CurrentUser, require_role
from app.core.database import get_db
from app.core.rate_limit import rate_limiter
from app.models.enums import UserRole
from app.schemas.common import PaginatedResponse


def build_list_router(
    *,
    prefix: str,
    tag: str,
    allowed_roles: Iterable[UserRole],
    service_factory: Callable[[AsyncSession], object],
    response_model: Type[BaseModel],
    summary: str,
) -> APIRouter:
    """An APIRouter exposing a single authenticated, role-gated `GET /`.

    Returns real rows through the real service, so an empty array here means
    the table is empty — not that the endpoint is stubbed. That distinction
    matters: a hardcoded `[]` would keep returning `[]` after Phase 1 starts
    inserting employees, and nobody would notice until a demo.
    """
    router = APIRouter(prefix=prefix, tags=[tag], dependencies=[Depends(rate_limiter)])

    @router.get("/", response_model=PaginatedResponse[response_model], summary=summary)
    async def list_entities(
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        db: AsyncSession = Depends(get_db),
        current_user: CurrentUser = Depends(require_role(allowed_roles)),
    ):
        service = service_factory(db)
        rows, total = await service.list(limit=limit, offset=offset)
        return PaginatedResponse(
            items=[response_model.model_validate(row) for row in rows],
            total=total,
            limit=limit,
            offset=offset,
        )

    return router
