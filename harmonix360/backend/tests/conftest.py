"""Shared fixtures.

These tests run against a REAL Postgres (the same one alembic migrated), never
SQLite. The two behaviours worth the most here — the Contract non-overlap
EXCLUDE constraint and the transaction-scoped advisory lock — are properties of
the database's constraint system and lock manager; neither can be exercised
against SQLite, and neither can be proven by a mock.
"""
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.department import Department
from app.models.user import User


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def session():
    async with AsyncSessionLocal() as s:
        yield s


@pytest_asyncio.fixture
async def department():
    """A committed Department, torn down afterwards.

    Committed, not rolled back: an Employee FKs into it, and a row that exists
    only inside an uncommitted transaction is invisible to the separate session
    the ASGI client's request handler opens.
    """
    async with AsyncSessionLocal() as s:
        dept = Department(public_id="temp", name="Test Engineering", code="TEST-ENG")
        s.add(dept)
        await s.flush()
        dept.public_id = f"dept_test_{dept.id}"
        await s.commit()
        dept_id, public_id = dept.id, dept.public_id

    yield public_id

    async with AsyncSessionLocal() as s:
        await s.execute(delete(Department).where(Department.id == dept_id))
        await s.commit()
