"""Request-scoped auth dependencies.

`require_role` is the single server-side gate Architecture §5's matrix is
enforced through. It is deliberately the *same* mechanism for every entry
point: the MCP tools added in Phase 9 authorize an agent against the
authenticated user's role through this same check rather than through a
looser agent-wide scope, and the offline-sync push path runs the service
methods these dependencies guard. There is no second permission model
(Architecture §9).
"""
from typing import Callable, Iterable, Optional

from fastapi import Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.config import settings
from app.core.security import decode_token
from app.models.enums import UserRole

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


class CurrentUser:
    """The authenticated principal for this request.

    `employee_public_id` is the Employee row this login owns, when one exists.
    It is the anchor for every "…but only their own" rule in Architecture §5:
    an Employee may read their own profile, attendance and leave balance, and
    the role check alone cannot express that — a role says *what kind* of
    access, this says *whose rows*. Services compare against it rather than
    trusting an employee id from the request body.
    """

    def __init__(
        self,
        email: str,
        role: UserRole,
        user_id: str = "usr_demo",
        employee_public_id: Optional[str] = None,
    ):
        self.email = email
        self.role = role
        self.user_id = user_id
        self.employee_public_id = employee_public_id

    def is_hr(self) -> bool:
        """True for any role with full HR CRUD (Architecture §5)."""
        from app.models.enums import HR_ROLES

        return self.role in HR_ROLES

    def __repr__(self) -> str:
        return f"CurrentUser(email={self.email!r}, role={self.role.value!r})"


async def get_current_user(token: Optional[str] = Depends(oauth2_scheme)) -> CurrentUser:
    if not token:
        if settings.ENVIRONMENT.lower() == "production":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # Non-production convenience only: an unauthenticated request is treated
        # as the demo admin so `curl`/Swagger work without a login round-trip.
        # Guarded on ENVIRONMENT so this can never be the production behaviour.
        return CurrentUser(email="admin@peoplepay360.com", role=UserRole.ADMIN, user_id="admin_1")

    payload = decode_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        role = UserRole(payload.get("role", UserRole.EMPLOYEE.value))
    except ValueError:
        # An unrecognised role claim fails CLOSED, to the least-privileged role.
        # A token minted before the role vocabulary changed must not be read as
        # "no constraint"; it must be read as "the weakest constraint".
        role = UserRole.EMPLOYEE

    return CurrentUser(
        email=payload["sub"],
        role=role,
        user_id=payload.get("sub"),
        employee_public_id=payload.get("employee_id"),
    )


def require_role(*allowed_roles: UserRole) -> Callable:
    """Dependency factory: 403 unless the caller holds one of `allowed_roles`.

    Accepts either loose arguments or a single iterable, so the named sets in
    `app.models.enums` (HR_ROLES, PAYROLL_ROLES, …) can be passed straight
    through: `Depends(require_role(HR_ROLES))`.

    ADMIN is granted implicitly — PRD §3 defines it as "full access to
    everything" — so no endpoint needs to remember to list it. Every other role
    must be named explicitly; there is no "or above" fallback, because the role
    ladder is not totally ordered (an HR Manager has full HR rights and zero
    payroll rights, so "HR Manager or above" and "HR Payroll User or above"
    are genuinely different questions). Making each endpoint state its own set
    keeps that distinction visible at the call site instead of hidden in a
    comparison operator.
    """
    resolved: set[UserRole] = set()
    for entry in allowed_roles:
        if isinstance(entry, UserRole):
            resolved.add(entry)
        elif isinstance(entry, Iterable):
            resolved.update(entry)
        else:  # pragma: no cover - guards a programming error, not user input
            raise TypeError(f"require_role expects UserRole values or iterables of them, got {entry!r}")

    if not resolved:
        raise ValueError("require_role() needs at least one allowed role")

    async def role_checker(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role is UserRole.ADMIN:
            return current_user
        if current_user.role not in resolved:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role '{current_user.role.value}' is not authorized for this operation. "
                    f"Requires one of: {sorted(r.value for r in resolved)}"
                ),
            )
        return current_user

    return role_checker


async def require_idempotency_key(
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
) -> str:
    """400 unless the caller supplied an `Idempotency-Key` header.

    Architecture §6 makes the key REQUIRED on Payrun create and compute, and
    "required" has to be enforced somewhere: `IdempotencyMiddleware` replays a
    key it is given but is silent about a request that omits one, which is
    precisely the double-clicked Compute the key exists to stop. This
    dependency is that enforcement, declared per-route so the requirement is
    visible in the OpenAPI schema and at the call site rather than buried in
    middleware.

    Deliberately only on the two operations §6 names. Making every POST in the
    product carry a key would train clients to generate one mechanically,
    which is how a client ends up REUSING one — and a reused key on a
    different operation is a replayed answer to a question nobody asked.
    """
    if not (idempotency_key or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "An 'Idempotency-Key' header is required for this operation so a retried or "
                "double-clicked request cannot run payroll twice (Architecture §6)."
            ),
        )
    return idempotency_key
