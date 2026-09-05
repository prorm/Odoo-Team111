import enum


class UserRole(str, enum.Enum):
    EMPLOYEE = "EMPLOYEE"
    ASSET_MANAGER = "ASSET_MANAGER"
    DEPARTMENT_HEAD = "DEPARTMENT_HEAD"
    ADMIN = "ADMIN"


class UserStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class DepartmentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class AuditActorType(str, enum.Enum):
    """Who performed an audited action. Referenced by the AI/MCP layer in
    Phase 9, where an agent-initiated mutation is audited with the same shape
    as a human one (Architecture §11)."""
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"
    AI = "AI"
