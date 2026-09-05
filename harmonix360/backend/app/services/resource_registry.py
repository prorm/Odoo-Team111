"""Generic registry mapping a booking's resource_type -> a resolver for that
type. This is the entire mechanism that keeps BookingService decoupled from
any specific resource model (Asset, MeetingRoom, ...): a new resource type is
"bookable" the moment something calls register_resource_type for it, nothing
in app/services/booking.py or app/repositories/booking.py needs to change.

Deliberately not a class hierarchy / plugin framework — a dict of callables
is the smallest thing that satisfies "given a resource_type + public_id, is
this a real, bookable resource, and what's its internal id?".
"""
from typing import Awaitable, Callable, Dict, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession

# Resolver contract: (session, resource_public_id) -> (internal_id, is_bookable)
# or None if no resource with that public_id exists.
ResourceResolver = Callable[[AsyncSession, str], Awaitable[Optional[Tuple[int, bool]]]]

_REGISTRY: Dict[str, ResourceResolver] = {}


def register_resource_type(resource_type: str, resolver: ResourceResolver) -> None:
    _REGISTRY[resource_type] = resolver


def get_resolver(resource_type: str) -> Optional[ResourceResolver]:
    return _REGISTRY.get(resource_type)


def registered_resource_types() -> list[str]:
    return list(_REGISTRY.keys())
