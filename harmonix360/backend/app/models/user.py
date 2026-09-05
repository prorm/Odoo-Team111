"""The authentication/authorization principal.

`User` is who logs in and what `require_role` checks. It is deliberately NOT
the HR record — that is `Employee` (app/models/employee.py), which carries the
department/manager/schedule/contract graph and points back here through an
optional `user_id`. Keeping them apart means an employee can exist in HR before
they have a login (or after it is revoked), and a payroll-only login can exist
with no Employee row at all.
"""
from datetime import datetime
from typing import Optional, TYPE_CHECKING
from sqlalchemy import BigInteger, String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from app.models.mixins import utc_now
from app.models.types import StrEnum
from app.models.enums import UserRole, UserStatus

if TYPE_CHECKING:
    from app.models.department import Department


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(StrEnum(UserRole), default=UserRole.EMPLOYEE, nullable=False, index=True)
    status: Mapped[UserStatus] = mapped_column(StrEnum(UserStatus), default=UserStatus.ACTIVE, nullable=False)
    department_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("departments.id"), nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    department: Mapped[Optional["Department"]] = relationship(
        "Department", back_populates="users", foreign_keys=[department_id]
    )
