from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db
from app.core.security import create_access_token, get_password_hash, verify_password
from app.models.enums import UserRole
from app.schemas.auth import Token, UserLogin, UserCreate, UserResponse

router = APIRouter(prefix="/auth", tags=["Auth"])

@router.post("/login", response_model=Token)
async def login(credentials: UserLogin):
    # Demo credentials validation for quick testing / vertical slice proof
    if credentials.email == "admin@harmonix360.com" and credentials.password == "admin123":
        token = create_access_token(subject=credentials.email, role=UserRole.ADMIN.value)
        return Token(access_token=token)
    elif credentials.email == "employee@harmonix360.com" and credentials.password == "emp123":
        token = create_access_token(subject=credentials.email, role=UserRole.EMPLOYEE.value)
        return Token(access_token=token)
    
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
