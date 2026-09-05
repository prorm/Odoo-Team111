"""Platform-layer tables — the ones that belong to no business domain.

What used to live here (Asset, AssetCategory, Allocation, TransferRequest,
ResourceBooking, MeetingRoom, MaintenanceRequest, AuditCycle, AuditAssetLog,
Discrepancy, Note) was AssetFlow's domain and was deleted wholesale; see
Architecture §2's "Removed" table. `User` and `Department` moved to
`app/models/user.py` / `app/models/department.py` when the models were split
per-domain (Architecture §3).

What is left is infrastructure every domain shares: the immutable audit log,
the activity feed, notifications, and the offline-sync idempotency log. None of
it references a domain entity by name, which is exactly why it survived the
domain swap untouched.
"""
from datetime import datetime
from typing import Optional
from sqlalchemy import BigInteger, String, DateTime, ForeignKey, Boolean, JSON, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.core.clock import server_utc_now
from app.models.mixins import utc_now


class Notification(Base):
    """Generic per-user notification row.

    Domain-agnostic by construction: `type` is a free string and the payload
    lives in `metadata_json`, so payroll ("your payslip is ready") and time-off
    ("your leave was approved") both use it without a schema change. The
    AssetFlow-specific producer task that used to write these
    (`jobs/tasks/notifications.py::send_asset_state_change_notification`) was
    deleted with its domain; Phase 5 adds the payroll producer.
    """
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column("metadata", JSON, nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class AuditLog(Base):
    """Append-only audit log (Architecture §11).

    UPDATE and DELETE are revoked from the runtime role in migration 001, so
    immutability is a database grant, not a code convention. Every audited
    action listed in Architecture §11 — contract changes, attendance
    corrections, leave approvals, payrun transitions, payslip generation, and
    later the AI/MCP/offline-sync mutations — lands in this one table, so a
    human action and an agent-initiated one are equally inspectable.
    """
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    entity: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    before_diff: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    after_diff: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)


class SyncMutation(Base):
    """Idempotency log for POST /sync/push (app/services/sync.py).

    Deliberately NOT keyed off the Idempotency-Key header / IdempotencyMiddleware
    (app/middleware/idempotency.py) — that caches one whole batch response
    keyed by one header value, so a client that retries with 9 of 10 mutations
    already applied would get its entire cached batch replayed verbatim,
    including outcomes for ops the retry never intended to touch. Deduplication
    here is per `client_mutation_id`, generated client-side per queued
    mutation, so a partial retry only replays the ops it actually resubmits.

    Not an AuditedEntity: it is never updated or soft-deleted, only inserted
    and read back for a replay check, so it doesn't need version/tenant/
    deleted_at machinery built for mutable domain rows.

    Dormant in Phase 0-7: `sync_entities.py` registers zero entity types until
    Phase 8 points the engine at `attendance` and `time_off_request`. The table
    and the engine stay in place meanwhile — the registry, not the schema, is
    what makes an entity syncable.
    """
    __tablename__ = "sync_mutations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    client_mutation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # String, not a `users.id` FK: CurrentUser.user_id is a demo-auth string
    # ("usr_demo", "admin_1") in non-production mode (app/api/v1/deps.py), not
    # reliably a real integer PK — same reasoning as AuditLog.actor being a
    # plain string rather than an FK.
    actor_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    op: Mapped[str] = mapped_column(String(16), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    result_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=server_utc_now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("actor_key", "client_mutation_id", name="uq_sync_mutation_actor_client_id"),
    )
    __mapper_args__ = {"eager_defaults": True}
