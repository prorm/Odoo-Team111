"""Seed the demo dataset.

Idempotent by design — running it twice must not create a second copy of
anything, because it is run by hand during development and again right before
a demo, and a duplicated Employee is exactly the kind of thing that only
surfaces on stage.

    python -m app.seed

Phase 0 seeds the identity layer only: one login per role from PRD §3, plus
the departments the HR domain hangs off. Later phases extend this with
employees, schedules, contracts, time-off types and a computed payrun so the
acceptance criterion "populated with representative data" is met end to end.
"""
import asyncio
import logging

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import encode_public_id, get_password_hash
from app.models.department import Department
from app.models.enums import UserRole
from app.models.user import User

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("peoplepay360.seed")

# One login per role, so the RBAC matrix can be demonstrated by logging in
# rather than by describing it. Passwords are obviously weak on purpose: this
# is demo data and the file is in version control. Nothing here is a production
# credential path — real accounts are created through the admin UI.
DEMO_USERS: list[tuple[str, str, str, UserRole]] = [
    ("admin@peoplepay360.com", "admin123", "Aditi Rao (Admin)", UserRole.ADMIN),
    ("payroll.manager@peoplepay360.com", "payroll123", "Meera Iyer (Payroll Manager)", UserRole.HR_PAYROLL_MANAGER),
    ("payroll.user@peoplepay360.com", "payroll123", "Sanjay Nair (Payroll User)", UserRole.HR_PAYROLL_USER),
    ("hr.manager@peoplepay360.com", "hrmanager123", "Priya Sharma (HR Manager)", UserRole.HR_MANAGER),
    ("employee@peoplepay360.com", "employee123", "Rahul Verma (Employee)", UserRole.EMPLOYEE),
]

DEMO_DEPARTMENTS: list[tuple[str, str]] = [
    ("Engineering", "ENG"),
    ("Sales", "SALES"),
    ("Human Resources", "HR"),
    ("Finance", "FIN"),
]


async def _seed_departments(db) -> int:
    created = 0
    for name, code in DEMO_DEPARTMENTS:
        existing = (
            await db.execute(select(Department).where(Department.code == code, Department.tenant_id == "default"))
        ).scalar_one_or_none()
        if existing:
            continue
        dept = Department(public_id="temp", name=name, code=code)
        db.add(dept)
        await db.flush()
        dept.public_id = encode_public_id(dept.id, "dept")
        created += 1
    await db.commit()
    return created


async def _seed_users(db) -> int:
    created = 0
    for email, password, name, role in DEMO_USERS:
        existing = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if existing:
            # Re-assert the role: the role vocabulary changed in Phase 0 step 3,
            # so a database seeded before that holds rows whose `role` no longer
            # parses. Fixing it here keeps re-running the seed a valid repair.
            if existing.role != role:
                existing.role = role
                created += 0
            continue
        user = User(
            public_id="temp",
            email=email,
            password_hash=get_password_hash(password),
            name=name,
            role=role,
        )
        db.add(user)
        await db.flush()
        user.public_id = encode_public_id(user.id, "usr")
        created += 1
    await db.commit()
    return created


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        departments = await _seed_departments(db)
        users = await _seed_users(db)

    logger.info("Seed complete: %d department(s), %d user(s) created.", departments, users)
    logger.info("Demo logins (email / password / role):")
    for email, password, _, role in DEMO_USERS:
        logger.info("  %-38s %-14s %s", email, password, role.value)


if __name__ == "__main__":
    asyncio.run(seed())
