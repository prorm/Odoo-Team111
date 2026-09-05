from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import BigInteger, String, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column, declared_attr
from app.core.clock import server_utc_now


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AuditedEntity:
    """Declarative mixin for the columns every audited entity repeats:
    id, public_id, tenant_id, created_at, updated_at, deleted_at, version.

    Column names/types match what Asset/TransferRequest/ResourceBooking already
    had inline, so mixing this in is a no-op at the DB level — Alembic autogenerate
    against this should produce an empty diff.

    `version` — REAL optimistic concurrency, not the 001-010 decorative column
    dropped in `011_drop_decorative_version` (nothing read/incremented/compared
    it then). This is the TODO from HARMONIX360_ARCHITECTURE.md Section 2 rule 10,
    built exactly as specified there: SQLAlchemy's `__mapper_args__["version_id_col"]`,
    which the ORM enforces on every UPDATE (`WHERE id=:id AND version=:loaded_version`,
    then bumps it) and raises `sqlalchemy.orm.exc.StaleDataError` when the row
    was already moved by someone else. Declared via `declared_attr` so all five
    concrete entities inherit the mapper arg once instead of repeating it.
    `BaseRepository.update`/`soft_delete` translate `StaleDataError` into a 409;
    see app/repositories/base.py.

    `updated_at` — `onupdate` is a Postgres-side expression (`server_utc_now()`,
    app/core/clock.py), not a Python callable. The offline-sync pull cursor
    orders rows by `(updated_at, id)`; if `updated_at` were computed by whichever
    app-tier worker happened to handle the request, clock drift between workers
    could put rows in an order the cursor comparison gets wrong. Delegating to
    the one Postgres engine every worker shares removes that source of drift.
    `created_at` keeps its DB-level `server_default=now()` from the table's
    creating migration (verified already present on all five tables) — no
    Python-side default is set here, so the ORM never overrides it with a
    client-computed value.
    """

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=server_utc_now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=server_utc_now(), onupdate=server_utc_now(), nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    @declared_attr
    def __mapper_args__(cls):
        # eager_defaults=True: created_at/updated_at are now DB-generated
        # (server_utc_now()), not Python-computed, so the ORM must read them
        # back via INSERT/UPDATE ... RETURNING at flush time or the in-memory
        # object is left with an "expired" attribute — any later access to
        # e.g. `entity.updated_at` (a sync attribute get) would then need a
        # lazy SELECT, which raises MissingGreenlet outside of an awaited
        # context. Explicit rather than relying on SQLAlchemy 2.0's "auto"
        # default, which is dialect-version-sensitive.
        return {"version_id_col": cls.version, "eager_defaults": True}
