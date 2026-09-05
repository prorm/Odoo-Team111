"""Delivery state is separate from immutable payroll results."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PayslipDelivery(Base):
    __tablename__ = "payslip_deliveries"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    payslip_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("payslips.id", ondelete="CASCADE"), unique=True
    )
    payrun_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("payruns.id", ondelete="CASCADE"), index=True
    )
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'sent', 'failed')", name="ck_delivery_status"
        ),
        CheckConstraint("attempts >= 0", name="ck_delivery_attempts"),
    )
