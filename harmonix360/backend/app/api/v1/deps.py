from typing import Callable, Optional
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_token
from app.models.enums import UserRole
from app.schemas.auth import UserResponse

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

class CurrentUser:
    def __init__(self, email: str, role: UserRole, user_id: str = "usr_demo"):
        self.email = email
        self.role = role
        self.user_id = user_id

async def get_current_user(token: Optional[str] = Depends(oauth2_scheme)) -> CurrentUser:
    if not token:
        if settings.ENVIRONMENT.lower() == "production":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Not authenticated",
                headers={"WWW-Authenticate": "Bearer"}
            )
        # Demo fallback for testing if no token header passed (non-production only)
        return CurrentUser(email="admin@harmonix360.com", role=UserRole.ADMIN, user_id="admin_1")

    payload = decode_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    role_str = payload.get("role", "EMPLOYEE")
    try:
        role = UserRole(role_str)
    except ValueError:
        role = UserRole.EMPLOYEE

    return CurrentUser(email=payload["sub"], role=role, user_id=payload.get("sub"))

def require_role(*allowed_roles: UserRole) -> Callable:
    async def role_checker(current_user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if current_user.role == UserRole.ADMIN:
            return current_user
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User role '{current_user.role}' is not authorized to perform this operation"
            )
        return current_user
    return role_checker
