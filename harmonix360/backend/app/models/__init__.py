"""Model package. Importing it loads every mapped class, which is what
`alembic/env.py` relies on to populate `Base.metadata` for autogenerate.

Platform tables live in `entities.py`; `user.py`/`department.py` hold the two
shared organisational models. HR domain modules are added in Phase 0 step 5.
"""
from app.core.database import Base
from app.models.enums import *  # noqa: F401,F403
from app.models.user import User
from app.models.department import Department
from app.models.entities import Notification, ActivityLog, AuditLog, SyncMutation

__all__ = [
    "Base",
    "User",
    "Department",
    "Notification",
    "ActivityLog",
    "AuditLog",
    "SyncMutation",
]
