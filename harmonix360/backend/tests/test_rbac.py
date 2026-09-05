"""The role system and `require_role`.

Architecture §5's matrix is the security boundary of this product — an
Employee must not be able to read a colleague's salary, and an HR Manager must
not be able to touch payroll. These tests pin the two pieces that decision
rests on: the role vocabulary itself, and the dependency that enforces it.

Deliberately unit-level and DB-free. The per-endpoint allow/deny tests live
next to the endpoints they guard (tests/test_employee_api.py etc.); what is
proven here is that the primitive underneath them behaves, so a failure points
at the mechanism rather than at one router.
"""
import pytest
from fastapi import HTTPException

from app.api.v1.deps import CurrentUser, require_role
from app.core.security import create_access_token, decode_token
from app.models.enums import (
    ADMIN_ROLES,
    ALL_ROLES,
    HR_ROLES,
    PAYROLL_ADMIN_ROLES,
    PAYROLL_ROLES,
    ROLE_HIERARCHY,
    UserRole,
    role_at_least,
)

ALL_FIVE = [
    UserRole.EMPLOYEE,
    UserRole.HR_MANAGER,
    UserRole.HR_PAYROLL_USER,
    UserRole.HR_PAYROLL_MANAGER,
    UserRole.ADMIN,
]


def _user(role: UserRole) -> CurrentUser:
    return CurrentUser(email=f"{role.value}@peoplepay360.com", role=role)


async def _check(dependency, role: UserRole) -> CurrentUser:
    """Invoke a require_role dependency directly, bypassing FastAPI's injection."""
    return await dependency(current_user=_user(role))


# ------------------------------------------------------- the vocabulary

def test_there_are_exactly_five_roles():
    """PRD §3 defines five. A sixth appearing means either the PRD moved or
    somebody reintroduced the generic asset-era vocabulary."""
    assert [r.value for r in UserRole] == [
        "employee",
        "hr_manager",
        "hr_payroll_user",
        "hr_payroll_manager",
        "admin",
    ]


def test_role_values_are_lowercase_and_match_architecture_section_4():
    for role in UserRole:
        assert role.value == role.value.lower()
        assert " " not in role.value


def test_hierarchy_covers_every_role_and_is_strictly_ordered():
    assert set(ROLE_HIERARCHY) == set(UserRole)
    ranks = [ROLE_HIERARCHY[r] for r in ALL_FIVE]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)


def test_role_at_least_reads_up_the_ladder():
    assert role_at_least(UserRole.ADMIN, UserRole.EMPLOYEE)
    assert role_at_least(UserRole.HR_PAYROLL_MANAGER, UserRole.HR_PAYROLL_USER)
    assert role_at_least(UserRole.HR_MANAGER, UserRole.HR_MANAGER)
    assert not role_at_least(UserRole.HR_MANAGER, UserRole.HR_PAYROLL_USER)
    assert not role_at_least(UserRole.EMPLOYEE, UserRole.HR_MANAGER)


# ------------------------------------------------- the named role sets

def test_named_role_sets_match_the_permission_matrix():
    """Architecture §5, read column by column."""
    # HR modules: everyone from HR Manager up, and nobody below.
    assert HR_ROLES == {
        UserRole.HR_MANAGER,
        UserRole.HR_PAYROLL_USER,
        UserRole.HR_PAYROLL_MANAGER,
        UserRole.ADMIN,
    }
    # Payruns/Payslips and read-only salary config start at HR Payroll User.
    assert PAYROLL_ROLES == {UserRole.HR_PAYROLL_USER, UserRole.HR_PAYROLL_MANAGER, UserRole.ADMIN}
    # Authoring Salary Structures/Rules starts at HR Payroll Manager.
    assert PAYROLL_ADMIN_ROLES == {UserRole.HR_PAYROLL_MANAGER, UserRole.ADMIN}
    assert ADMIN_ROLES == {UserRole.ADMIN}
    assert ALL_ROLES == set(UserRole)


def test_hr_manager_has_no_payroll_access():
    """The single most important negative in the matrix: PRD §3 gives HR
    Manager full HR CRUD and explicitly 'No payroll access'."""
    assert UserRole.HR_MANAGER in HR_ROLES
    assert UserRole.HR_MANAGER not in PAYROLL_ROLES
    assert UserRole.HR_MANAGER not in PAYROLL_ADMIN_ROLES


def test_employee_has_no_module_level_access():
    """An Employee's rights are all row-scoped ('own profile/attendance/leave
    balance'), so they appear in no module-level set."""
    for role_set in (HR_ROLES, PAYROLL_ROLES, PAYROLL_ADMIN_ROLES, ADMIN_ROLES):
        assert UserRole.EMPLOYEE not in role_set


# --------------------------------------------------------- require_role

async def test_require_role_allows_a_listed_role():
    dep = require_role(UserRole.HR_MANAGER)
    result = await _check(dep, UserRole.HR_MANAGER)
    assert result.role is UserRole.HR_MANAGER


async def test_require_role_denies_an_unlisted_role_with_403():
    dep = require_role(UserRole.HR_MANAGER)
    with pytest.raises(HTTPException) as exc:
        await _check(dep, UserRole.EMPLOYEE)
    assert exc.value.status_code == 403
    assert "employee" in exc.value.detail
    assert "hr_manager" in exc.value.detail


async def test_admin_is_allowed_everywhere_without_being_listed():
    """PRD §3: Admin has 'full access to everything'. No endpoint should have
    to remember to name it, and forgetting to must not lock admins out."""
    dep = require_role(UserRole.EMPLOYEE)
    assert (await _check(dep, UserRole.ADMIN)).role is UserRole.ADMIN


async def test_require_role_accepts_a_named_set():
    dep = require_role(HR_ROLES)
    for role in HR_ROLES:
        assert (await _check(dep, role)).role is role
    with pytest.raises(HTTPException) as exc:
        await _check(dep, UserRole.EMPLOYEE)
    assert exc.value.status_code == 403


async def test_require_role_accepts_mixed_arguments():
    dep = require_role(UserRole.EMPLOYEE, PAYROLL_ROLES)
    assert (await _check(dep, UserRole.EMPLOYEE)).role is UserRole.EMPLOYEE
    assert (await _check(dep, UserRole.HR_PAYROLL_USER)).role is UserRole.HR_PAYROLL_USER
    with pytest.raises(HTTPException):
        await _check(dep, UserRole.HR_MANAGER)


async def test_payroll_endpoints_deny_hr_manager_and_employee():
    """The two denials that matter most, exercised through the real dependency
    rather than asserted against the sets alone."""
    dep = require_role(PAYROLL_ROLES)
    for denied in (UserRole.EMPLOYEE, UserRole.HR_MANAGER):
        with pytest.raises(HTTPException) as exc:
            await _check(dep, denied)
        assert exc.value.status_code == 403
    for allowed in (UserRole.HR_PAYROLL_USER, UserRole.HR_PAYROLL_MANAGER, UserRole.ADMIN):
        assert (await _check(dep, allowed)).role is allowed


async def test_salary_rule_authoring_denies_payroll_user():
    """HR Payroll User is read-only on Salary Structures/Rules; only HR Payroll
    Manager (and Admin) may author them."""
    dep = require_role(PAYROLL_ADMIN_ROLES)
    with pytest.raises(HTTPException) as exc:
        await _check(dep, UserRole.HR_PAYROLL_USER)
    assert exc.value.status_code == 403
    assert (await _check(dep, UserRole.HR_PAYROLL_MANAGER)).role is UserRole.HR_PAYROLL_MANAGER


def test_require_role_rejects_an_empty_allow_list():
    """An endpoint that names no role is a bug — almost certainly a deleted
    constant — and must fail at import, not silently allow everyone."""
    with pytest.raises(ValueError):
        require_role()


# ------------------------------------------------------------- the token

def test_token_round_trips_role_and_employee_id():
    token = create_access_token(
        subject="hr.manager@peoplepay360.com",
        role=UserRole.HR_MANAGER.value,
        employee_id="emp_abc123",
    )
    payload = decode_token(token)
    assert payload["sub"] == "hr.manager@peoplepay360.com"
    assert payload["role"] == "hr_manager"
    assert payload["employee_id"] == "emp_abc123"
    assert UserRole(payload["role"]) is UserRole.HR_MANAGER


def test_token_omits_employee_id_when_the_user_has_no_employee_record():
    payload = decode_token(create_access_token(subject="admin@x.local", role=UserRole.ADMIN.value))
    assert "employee_id" not in payload


async def test_an_unrecognised_role_claim_fails_closed_to_employee():
    """A token minted before the role vocabulary changed must degrade to the
    LEAST privilege, never to 'unconstrained'."""
    from app.api.v1.deps import get_current_user

    stale = create_access_token(subject="old@peoplepay360.com", role="ASSET_MANAGER")
    user = await get_current_user(token=stale)
    assert user.role is UserRole.EMPLOYEE
