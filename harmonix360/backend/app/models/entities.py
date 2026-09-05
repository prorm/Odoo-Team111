from datetime import datetime
from decimal import Decimal
from typing import Optional, List
from sqlalchemy import (
    BigInteger, String, DateTime, Enum as SQLEnum, ForeignKey, Boolean, Numeric,
    Index, UniqueConstraint, JSON, Text, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base
from app.core.clock import server_utc_now
from app.models.mixins import AuditedEntity, utc_now
from app.models.enums import (
    UserRole, UserStatus, AssetStatus, AssetCondition, AllocationStatus,
    TransferStatus, DepartmentStatus, DiscrepancyType, DiscrepancyStatus,
    BookingStatus, MaintenanceStatus, AuditCycleStatus, AuditLogStatus
)

def StrEnum(enum_cls, **kwargs):
    return SQLEnum(enum_cls, native_enum=False, values_callable=lambda x: [e.value for e in x], **kwargs)

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

    department: Mapped[Optional["Department"]] = relationship("Department", back_populates="users", foreign_keys=[department_id])

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
    assets: Mapped[List["Asset"]] = relationship("Asset", back_populates="department")

    __table_args__ = (
        UniqueConstraint("code", "tenant_id", name="uq_department_code_tenant"),
    )

class AssetCategory(Base):
    __tablename__ = "asset_categories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    assets: Mapped[List["Asset"]] = relationship("Asset", back_populates="category")

class Asset(AuditedEntity, Base):
    __tablename__ = "assets"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_tag: Mapped[str] = mapped_column(String(128), nullable=False)
    serial_number: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    category_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("asset_categories.id"), nullable=False)
    department_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("departments.id"), nullable=True)
    status: Mapped[AssetStatus] = mapped_column(StrEnum(AssetStatus), default=AssetStatus.AVAILABLE, nullable=False, index=True)
    condition: Mapped[AssetCondition] = mapped_column(StrEnum(AssetCondition), default=AssetCondition.GOOD, nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_bookable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    purchase_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Money is Numeric/Decimal, never Float — Section 2 rule 9. Float(53) cannot
    # represent 0.10 exactly, so summing costs drifts; asyncpg returns Numeric
    # as decimal.Decimal, keeping arithmetic exact end-to-end.
    purchase_cost: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)

    category: Mapped["AssetCategory"] = relationship("AssetCategory", back_populates="assets")
    department: Mapped[Optional["Department"]] = relationship("Department", back_populates="assets")
    allocations: Mapped[List["Allocation"]] = relationship("Allocation", back_populates="asset")
    maintenance_requests: Mapped[List["MaintenanceRequest"]] = relationship("MaintenanceRequest", back_populates="asset")
    transfers: Mapped[List["TransferRequest"]] = relationship("TransferRequest", back_populates="asset")

    __table_args__ = (
        UniqueConstraint("asset_tag", "tenant_id", name="uq_asset_tag_tenant"),
    )

class Allocation(Base):
    __tablename__ = "allocations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    asset_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("assets.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    allocated_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    allocated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    due_date: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    returned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    return_condition: Mapped[Optional[AssetCondition]] = mapped_column(StrEnum(AssetCondition), nullable=True)
    check_in_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_overdue: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    overdue_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[AllocationStatus] = mapped_column(StrEnum(AllocationStatus), default=AllocationStatus.ACTIVE, nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    asset: Mapped["Asset"] = relationship("Asset", back_populates="allocations")

class TransferRequest(AuditedEntity, Base):
    __tablename__ = "transfer_requests"

    asset_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("assets.id"), nullable=False)
    from_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    to_user_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    requested_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    approved_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    status: Mapped[TransferStatus] = mapped_column(StrEnum(TransferStatus), default=TransferStatus.PENDING, nullable=False, index=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    decision_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ai_decision_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    asset: Mapped["Asset"] = relationship("Asset", back_populates="transfers")

class ResourceBooking(AuditedEntity, Base):
    """Generic booking for any resource type (asset, meeting_room, ...).

    resource_type/resource_id form a polymorphic reference rather than a FK
    to a single table — bookable resources are registered via
    app/services/resource_registry.py, not hardcoded here. See
    resource_bookings_range_overlap_excl (raw SQL, alembic/versions) for the
    Postgres EXCLUDE constraint that actually prevents double-booking. It is
    predicated on `deleted_at IS NULL AND status <> 'CANCELLED'` (migration
    010_booking_exclusion_predicate), so cancelled and soft-deleted rows release
    their slot; PENDING rows still hold theirs until explicitly cancelled.

    Consequence for anything that mutates `status`: confirming a CANCELLED
    booking moves the row back INTO the constrained set and can raise SQLSTATE
    23P01. Every such path owes the translation described in BaseService's
    docstring — app/services/booking.py does this in both of its mutation
    methods.
    """
    __tablename__ = "resource_bookings"

    resource_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[BookingStatus] = mapped_column(StrEnum(BookingStatus), default=BookingStatus.PENDING, nullable=False, index=True)
    purpose: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class MeetingRoom(AuditedEntity, Base):
    """Minimal second resource type — proves ResourceBooking has no Asset-specific
    logic left. Deliberately has no service/router/schemas: the task is to prove
    genericity, not to build a second ERP module."""
    __tablename__ = "meeting_rooms"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_bookable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

class MaintenanceRequest(Base):
    __tablename__ = "maintenance_requests"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    asset_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("assets.id"), nullable=False)
    requested_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(32), default="MEDIUM", nullable=False)
    status: Mapped[MaintenanceStatus] = mapped_column(StrEnum(MaintenanceStatus), default=MaintenanceStatus.PENDING, nullable=False, index=True)
    approved_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    asset: Mapped["Asset"] = relationship("Asset", back_populates="maintenance_requests")

class AuditCycle(Base):
    __tablename__ = "audit_cycles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scope_dept_id: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("departments.id"), nullable=True)
    scope_location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    start_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[AuditCycleStatus] = mapped_column(StrEnum(AuditCycleStatus), default=AuditCycleStatus.PLANNED, nullable=False, index=True)
    auditor_ids: Mapped[Optional[dict]] = mapped_column(JSON, default=list, nullable=True)
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=False)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

class AuditAssetLog(Base):
    __tablename__ = "audit_asset_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    audit_cycle_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("audit_cycles.id"), nullable=False, index=True)
    asset_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("assets.id"), nullable=False)
    expected_location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    actual_location: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[AuditLogStatus] = mapped_column(StrEnum(AuditLogStatus), default=AuditLogStatus.PENDING, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verified_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

class Discrepancy(Base):
    __tablename__ = "discrepancies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    audit_cycle_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("audit_cycles.id"), nullable=False, index=True)
    asset_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("assets.id"), nullable=False)
    type: Mapped[DiscrepancyType] = mapped_column(StrEnum(DiscrepancyType), nullable=False)
    status: Mapped[DiscrepancyStatus] = mapped_column(StrEnum(DiscrepancyStatus), default=DiscrepancyStatus.OPEN, nullable=False, index=True)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[Optional[int]] = mapped_column(BigInteger, ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

class Notification(Base):
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
    """
    Immutable audit log table per Architecture Section 2 rule 4 & Section 3.
    Must be insert-only; UPDATE & DELETE permissions revoked in migration.
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

# NOTE: AuditEvent (audit_events table) was a duplicate of AuditLog — same
# purpose (immutable mutation record), never written to or read from anywhere
# in the codebase (only AuditLog is used, via AuditLogger). Removed rather than
# kept as dead schema; see alembic/versions/003_scaffolding_cleanup.py.


class Note(AuditedEntity, Base):
    """Proof-of-concept entity: fully CRUD-able through BaseRepository/BaseService alone."""
    __tablename__ = "notes"

    content: Mapped[str] = mapped_column(Text, nullable=False)


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
