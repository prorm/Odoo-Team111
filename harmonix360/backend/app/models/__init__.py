"""Model package. Importing it loads every mapped class, which is what
`alembic/env.py` relies on to populate `Base.metadata` for autogenerate — and
what makes SQLAlchemy's string-based relationship resolution work, since a
relationship declared as `"Employee"` can only be resolved once that class has
been registered.

Layout follows Architecture §3:
  entities.py            platform tables (audit, activity, notifications, sync)
  user.py, department.py shared organisational models
  employee.py … payroll.py   the HR domain
"""
from app.core.database import Base
from app.models.enums import *  # noqa: F401,F403

# Order matters only for readability; SQLAlchemy resolves the string-named
# relationships between them after all are registered.
from app.models.user import User
from app.models.department import Department
from app.models.entities import ActivityLog, AuditLog, Notification, SyncMutation
from app.models.working_schedule import ScheduleLine, WorkingSchedule
from app.models.employee import Employee
from app.models.salary import SalaryRule, SalaryStructure, SalaryStructureRule
from app.models.contract import Contract
from app.models.attendance import Attendance
from app.models.time_off import TimeOffAllocation, TimeOffRequest, TimeOffType
from app.models.payroll import Payrun, PayrunEmployee, Payslip, PayslipLine

__all__ = [
    "Base",
    # platform
    "ActivityLog",
    "AuditLog",
    "Notification",
    "SyncMutation",
    # shared organisational
    "User",
    "Department",
    # HR domain
    "Employee",
    "Contract",
    "WorkingSchedule",
    "ScheduleLine",
    "Attendance",
    "TimeOffType",
    "TimeOffAllocation",
    "TimeOffRequest",
    "SalaryStructure",
    "SalaryStructureRule",
    "SalaryRule",
    "Payrun",
    "PayrunEmployee",
    "Payslip",
    "PayslipLine",
]
