from pydantic import BaseModel, EmailStr
from app.models.enums import UserRole

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    name: str
    role: UserRole = UserRole.EMPLOYEE

class UserResponse(BaseModel):
    id: str
    email: EmailStr
    name: str
    role: UserRole
    tenant_id: str

    class Config:
        from_attributes = True
