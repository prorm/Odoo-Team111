"""Login and identity.

The token carries three claims the rest of the app depends on: `sub` (email),
`role` (one of PRD §3's five), and `employee_id` — the Employee row this login
owns, when there is one. That last claim is what lets an "own records only"
rule be enforced without a database round-trip on every request, and it is why
Architecture §5's first row ("own profile/attendance/leave balance") can be
checked at the service layer rather than trusted from a request body.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import CurrentUser, get_current_user
from app.core.database import get_db
from app.core.security import create_access_token, verify_password
from app.models.enums import UserRole, UserStatus
from app.models.user import User
from app.schemas.auth import Token, UserLogin, UserResponse

router = APIRouter(prefix="/auth", tags=["Auth"])


async def _employee_public_id_for(db: AsyncSession, user: User) -> str | None:
    """The Employee row this user owns, if the HR domain is built yet.

    Imported lazily and tolerated as absent so that auth keeps working in a
    tree where the Employee model has not landed (Phase 0 step 5) — login must
    not be the thing that breaks while the domain is being built out.
    """
    try:
        from app.models.employee import Employee
    except ImportError:  # pragma: no cover - only before Phase 0 step 5
        return None

    row = await db.execute(select(Employee).where(Employee.user_id == user.id, Employee.deleted_at.is_(None)))
    employee = row.scalar_one_or_none()
    return employee.public_id if employee else None


@router.post("/login", response_model=Token)
async def login(credentials: UserLogin, db: AsyncSession = Depends(get_db)):
    """Authenticate against the users table.

    The failure paths deliberately return one indistinguishable message. A
    distinct "no such user" would let anyone enumerate who works here by
    probing addresses, which on an HR system is itself the sensitive data.
    """
    result = await db.execute(
        select(User).where(User.email == credentials.email.lower(), User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()

    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if user is None or not verify_password(credentials.password, user.password_hash):
        raise invalid
    if user.status is not UserStatus.ACTIVE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This account is inactive")

    token = create_access_token(
        subject=user.email,
        role=user.role.value,
        employee_id=await _employee_public_id_for(db, user),
    )
    return Token(access_token=token)


@router.get("/me", response_model=UserResponse)
async def read_current_user(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Who the caller is, as the server sees them.

    The frontend uses this to decide which nav entries and actions to render.
    That is presentation only — every one of those actions is independently
    role-checked server-side, so hiding a button is a courtesy, never a control.
    """
    result = await db.execute(select(User).where(User.email == current_user.email, User.deleted_at.is_(None)))
    user = result.scalar_one_or_none()

    if user is None:
        # The non-production demo principal (deps.get_current_user) has no row.
        return UserResponse(
            id="usr_demo",
            email=current_user.email,
            name="Demo Administrator",
            role=current_user.role,
            tenant_id="default",
            employee_id=current_user.employee_public_id,
        )

    return UserResponse(
        id=user.public_id,
        email=user.email,
        name=user.name,
        role=user.role,
        tenant_id=user.tenant_id,
        employee_id=await _employee_public_id_for(db, user),
    )


@router.get("/roles", response_model=list[str])
async def list_roles():
    """The five roles, for populating a role picker in the Admin UI."""
    return [role.value for role in UserRole]
