"""Login, over the real API, against the real users table.

Complements tests/test_rbac.py: that file proves the role primitive behaves,
this one proves a person can actually obtain a token carrying the right role —
which is what every downstream allow/deny test depends on.

Requires the demo users from `python -m app.seed`. The fixture seeds them if
they are missing, so a fresh CI database needs no extra step.
"""
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.core.security import decode_token
from app.models.enums import UserRole
from app.models.user import User
from app.seed import DEMO_USERS, seed


@pytest_asyncio.fixture(scope="session", autouse=True)
async def demo_users():
    """Ensure the five demo logins exist. `seed()` is idempotent."""
    async with AsyncSessionLocal() as db:
        present = (await db.execute(select(User.email))).scalars().all()
    if not set(e for e, *_ in DEMO_USERS).issubset(set(present)):
        await seed()


async def _login(client, email: str, password: str):
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


@pytest.mark.parametrize("email,password,_name,role", DEMO_USERS)
async def test_each_role_can_log_in_and_gets_its_role_in_the_token(client, email, password, _name, role):
    """All five roles, end to end. The token's `role` claim is what
    `require_role` reads, so a wrong claim here silently misgrants every
    downstream endpoint."""
    resp = await _login(client, email, password)
    assert resp.status_code == 200, resp.text

    payload = decode_token(resp.json()["access_token"])
    assert payload["sub"] == email
    assert UserRole(payload["role"]) is role


async def test_wrong_password_is_rejected(client):
    resp = await _login(client, "hr.manager@peoplepay360.com", "not-the-password")
    assert resp.status_code == 401


async def test_unknown_email_is_indistinguishable_from_a_wrong_password(client):
    """Different messages would let anyone enumerate who works here by probing
    addresses — on an HR system the employee list is itself sensitive."""
    unknown = await _login(client, "nobody@peoplepay360.com", "whatever")
    wrong_password = await _login(client, "hr.manager@peoplepay360.com", "whatever")

    assert unknown.status_code == wrong_password.status_code == 401
    assert unknown.json()["detail"] == wrong_password.json()["detail"]


async def test_me_reports_the_authenticated_role(client):
    token = (await _login(client, "payroll.user@peoplepay360.com", "payroll123")).json()["access_token"]
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == "payroll.user@peoplepay360.com"
    assert body["role"] == UserRole.HR_PAYROLL_USER.value


async def test_refresh_extends_an_active_session(client):
    """The sliding session: a still-valid token buys a later-expiring one.

    Without this the 15-minute lifetime logged people out mid-payrun with no
    way back but a manual re-login.
    """
    original = (
        await _login(client, "payroll.manager@peoplepay360.com", "payroll123")
    ).json()["access_token"]

    resp = await client.post(
        "/api/v1/auth/refresh", headers={"Authorization": f"Bearer {original}"}
    )
    assert resp.status_code == 200, resp.text
    renewed = resp.json()["access_token"]

    before, after = decode_token(original), decode_token(renewed)
    assert after["exp"] >= before["exp"], "a refresh that does not extend is not a refresh"
    assert after["sub"] == before["sub"]
    # Re-read from the database, not copied from the old claims -- so a role
    # change or a deactivation takes effect within one token lifetime.
    assert after["role"] == UserRole.HR_PAYROLL_MANAGER.value

    reused = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {renewed}"}
    )
    assert reused.status_code == 200, reused.text


async def test_refresh_without_a_token_is_refused(client):
    """The load-bearing one.

    `get_current_user` treats an unauthenticated request as the demo admin
    outside production, so a refresh route built on that dependency would hand
    an admin token to anyone who can reach the port. This route reads the
    bearer token itself for exactly that reason.
    """
    resp = await client.post("/api/v1/auth/refresh")
    assert resp.status_code == 401, resp.text
    assert "access_token" not in resp.text


async def test_refresh_rejects_a_token_it_did_not_sign(client):
    """An expired or forged token cannot be traded for a live one — otherwise
    the short lifetime would bound nothing, and an idle session would never
    actually end."""
    forged = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJleHAiOjE3MDAwMDAwMDAsInN1YiI6ImFkbWluQHBlb3BsZXBheTM2MC5jb20iLCJyb2xlIjoiYWRtaW4ifQ."
        "not-a-real-signature"
    )
    resp = await client.post(
        "/api/v1/auth/refresh", headers={"Authorization": f"Bearer {forged}"}
    )
    assert resp.status_code == 401, resp.text
    assert "access_token" not in resp.text


async def test_roles_endpoint_lists_exactly_the_five_roles(client):
    resp = await client.get("/api/v1/auth/roles")
    assert resp.status_code == 200
    assert resp.json() == ["employee", "hr_manager", "hr_payroll_user", "hr_payroll_manager", "admin"]
