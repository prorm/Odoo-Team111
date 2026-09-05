"""Who an MCP call is acting as, and what that identity is allowed to do.

Architecture §5, verbatim: "an MCP session is bound to an authenticated user's
role, since the demo scenario is 'Claude acting as/for a specific HR user,' not
an anonymous agent." So every tool takes an `actor_email`, that email is
resolved to a REAL `User` row, and the resulting `CurrentUser` is the same
object a REST request handler would have been given.

RBAC IS NOT REIMPLEMENTED HERE
------------------------------
`authorize()` calls `app.api.v1.deps.require_role` — the identical dependency
factory the routers use — and invokes the checker it returns. Not a copy of its
logic, not a parallel table of permissions: the same function object, raising
the same `HTTPException(403)` with the same message. If §5's matrix changes in
`deps.py`, MCP's answer changes with it in the same commit, which is the only
arrangement that keeps "there is no second permission model" true over time
rather than just true today.

The agent API key (`MCP_AGENT_API_KEY`, checked by `@mcp_tool`) and this
identity are different things and both are required. The key says "this process
may talk to the MCP server at all"; the actor says "and it is acting as this
person, with exactly their rights." A valid key with an actor who lacks the role
is still a 403 — the key never widens what the actor can do.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import CurrentUser, require_role
from app.models.employee import Employee
from app.models.enums import UserRole, UserStatus
from app.models.user import User


async def resolve_actor(session: AsyncSession, actor_email: str) -> CurrentUser:
    """The `CurrentUser` for `actor_email`, or 401.

    There is deliberately NO development fallback here. `get_current_user`
    treats an unauthenticated HTTP request as the demo admin outside production
    so that curl and Swagger work; doing the same for MCP would mean an agent
    that supplied no actor silently got admin rights, which is precisely the
    "looser agent-wide scope" §5 rules out.

    The employee link is resolved the same way `POST /auth/login` resolves it,
    so an Employee acting through MCP is scoped to their own records by the very
    same `employee_for` check that scopes them through REST.
    """
    email = (actor_email or "").strip().lower()
    if not email:
        raise HTTPException(
            401,
            "An MCP tool call must name the user it is acting as (actor_email). "
            "Agent access is bound to a real user's role (Architecture §5).",
        )

    user = (
        await session.execute(
            select(User).where(User.email == email, User.deleted_at.is_(None))
        )
    ).scalars().first()
    if user is None:
        raise HTTPException(401, f"No PeoplePay360 user exists for '{email}'.")
    if user.status is not UserStatus.ACTIVE:
        raise HTTPException(403, f"The account '{email}' is not active.")

    employee_public_id = (
        await session.execute(
            select(Employee.public_id).where(
                Employee.user_id == user.id, Employee.deleted_at.is_(None)
            )
        )
    ).scalars().first()

    return CurrentUser(
        email=user.email,
        role=user.role,
        user_id=user.email,
        employee_public_id=employee_public_id,
    )


async def authorize(user: CurrentUser, *allowed) -> CurrentUser:
    """Apply §5's matrix to an already-resolved actor.

    Raises the router's own 403. Returns the user so a tool body can read as
    `user = await authorize(await resolve_actor(...), PAYROLL_ROLES)`.
    """
    checker = require_role(*allowed)
    return await checker(current_user=user)


async def actor_with_role(session: AsyncSession, actor_email: str, *allowed) -> CurrentUser:
    """resolve_actor + authorize, the shape most tools want."""
    user = await resolve_actor(session, actor_email)
    if allowed:
        await authorize(user, *allowed)
    return user


def is_self_service(user: CurrentUser, employee_public_id: str) -> bool:
    """True when an Employee-role actor is asking about their own record.

    §5 grants the Employee role read access to their own profile, attendance and
    leave balance, and nothing else. This is the "whose rows" half of that rule;
    `require_role` only answers "what kind of access".
    """
    return (
        user.role is UserRole.EMPLOYEE
        and bool(user.employee_public_id)
        and user.employee_public_id == employee_public_id
    )
