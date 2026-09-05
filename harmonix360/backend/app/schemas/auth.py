from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr

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
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    name: str
    role: UserRole
    tenant_id: str
    #: The Employee record this login owns, when one exists. Null for a
    #: payroll-only or admin login with no HR record of their own.
    employee_id: Optional[str] = None
