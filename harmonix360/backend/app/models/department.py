"""Organisational unit.

Reused from the platform foundation unchanged in shape — name/code/head/status
is exactly what PeoplePay360 needs for an Employee's department, a Contract's
department, and the dashboard's "salary cost by department" breakdown
(PRD B9). The only edit the HR migration made was dropping the `assets`
relationship that pointed at the deleted AssetFlow tables; no AssetFlow-specific
COLUMN ever existed on this table, so the schema itself is untouched.
"""
from datetime import datetime
from typing import Optional, List
from sqlalchemy import BigInteger, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from app.models.mixins import utc_now
from app.models.types import StrEnum
from app.models.enums import DepartmentStatus
from app.models.user import User


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    head_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    status: Mapped[DepartmentStatus] = mapped_column(StrEnum(DepartmentStatus), default=DepartmentStatus.ACTIVE, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    users: Mapped[List["User"]] = relationship("User", back_populates="department", foreign_keys=[User.department_id])
    # `employees` is added in Phase 0 step 5, alongside the Employee model.

    __table_args__ = (
        UniqueConstraint("code", "tenant_id", name="uq_department_code_tenant"),
    )
