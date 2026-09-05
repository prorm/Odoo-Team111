from typing import Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class ResponseEnvelope(BaseModel, Generic[T]):
    data: Optional[T] = None
    error: Optional[dict] = None


class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    total: int
    limit: int
    offset: int


class ORMModel(BaseModel):
    """Base for read schemas built straight off a SQLAlchemy row.

    `from_attributes` lets a router do `Model.model_validate(entity)` instead
    of hand-copying every field, which is where a renamed column quietly stops
    reaching the client.

    Note that `public_id` is exposed as `id`: the integer primary key never
    leaves the server. Each read schema declares that alias itself rather than
    inheriting a mapping here, because a few of them (audit payloads) need the
    raw name.
    """

    model_config = ConfigDict(from_attributes=True)
