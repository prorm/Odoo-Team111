"""The Phase 0 domain skeleton, checked against Architecture §4 and §2.

These are structural assertions, not behaviour tests. They exist because the
skeleton's mistakes are all silent: a missing `version` column disables
optimistic concurrency with no error, a duplicated `public_id` prefix makes one
entity's ids decode to another's rows, and an unmounted router just 404s. Each
of those would surface as a confusing bug three phases from now rather than as
a failure here.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.security import create_access_token
from app.main import app
from app.models import Base
from app.models.attendance import Attendance
from app.models.contract import Contract
from app.models.employee import Employee
from app.models.enums import UserRole
from app.models.payroll import Payrun, PayrunEmployee, Payslip, PayslipLine
from app.models.salary import SalaryRule, SalaryStructure, SalaryStructureRule
from app.models.time_off import TimeOffAllocation, TimeOffRequest, TimeOffType
from app.models.working_schedule import ScheduleLine, WorkingSchedule
from app.repositories.hr import ALL_REPOSITORIES

#: Architecture §4's entity list. Everything here must exist and be mapped.
DOMAIN_MODELS = [
    Employee,
    Contract,
    WorkingSchedule,
    ScheduleLine,
    Attendance,
    TimeOffType,
    TimeOffAllocation,
    TimeOffRequest,
    SalaryStructure,
    SalaryStructureRule,
    SalaryRule,
    Payrun,
    PayrunEmployee,
    Payslip,
    PayslipLine,
]

#: "All entities except pure line-item children get AuditedEntity
#: (audit + version_id_col)" — Architecture §4. These are the children.
LINE_ITEM_CHILDREN = [ScheduleLine, SalaryStructureRule, PayrunEmployee, PayslipLine]

#: Architecture §2's prefix table, verbatim.
EXPECTED_PREFIXES = {
    "Employee": "emp",
    "Contract": "ctr",
    "WorkingSchedule": "wsch",
    "Attendance": "att",
    "TimeOffType": "tot",
    "TimeOffAllocation": "alloc",
    "TimeOffRequest": "req",
    "SalaryStructure": "sstr",
    "SalaryRule": "srule",
    "Payrun": "prun",
    "Payslip": "pslip",
}

#: Every mounted HR list endpoint, with the role sets Architecture §5 allows
#: and denies. Kept as data so adding an entity means adding one row, and the
#: allow/deny pair for it cannot be forgotten independently.
LIST_ENDPOINTS = [
    ("/api/v1/employees/", UserRole.HR_MANAGER, UserRole.EMPLOYEE),
    ("/api/v1/contracts/", UserRole.HR_MANAGER, UserRole.EMPLOYEE),
    ("/api/v1/working-schedules/", UserRole.HR_MANAGER, UserRole.EMPLOYEE),
    ("/api/v1/attendance/", UserRole.HR_MANAGER, UserRole.EMPLOYEE),
    ("/api/v1/time-off-types/", UserRole.HR_MANAGER, UserRole.EMPLOYEE),
    ("/api/v1/time-off-allocations/", UserRole.HR_MANAGER, UserRole.EMPLOYEE),
    ("/api/v1/time-off-requests/", UserRole.HR_MANAGER, UserRole.EMPLOYEE),
    # Salary config and payroll start at HR Payroll User, and HR Manager is the
    # role that must be DENIED — the sharpest line in the matrix.
    ("/api/v1/salary-structures/", UserRole.HR_PAYROLL_USER, UserRole.HR_MANAGER),
    ("/api/v1/salary-rules/", UserRole.HR_PAYROLL_USER, UserRole.HR_MANAGER),
    ("/api/v1/payruns/", UserRole.HR_PAYROLL_USER, UserRole.HR_MANAGER),
    ("/api/v1/payslips/", UserRole.HR_PAYROLL_USER, UserRole.HR_MANAGER),
]


def _token(role: UserRole) -> dict[str, str]:
    token = create_access_token(subject=f"{role.value}@peoplepay360.com", role=role.value)
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------ models

@pytest.mark.parametrize("model", DOMAIN_MODELS, ids=lambda m: m.__name__)
def test_every_architecture_section_4_entity_is_mapped(model):
    assert model.__tablename__ in Base.metadata.tables


@pytest.mark.parametrize(
    "model", [m for m in DOMAIN_MODELS if m not in LINE_ITEM_CHILDREN], ids=lambda m: m.__name__
)
def test_non_child_entities_carry_the_audited_entity_columns(model):
    """AuditedEntity is what gives an entity real optimistic concurrency
    (`version_id_col`) and soft deletes. Missing it fails silently: concurrent
    edits stop conflicting and just overwrite each other."""
    columns = {c.name for c in model.__table__.columns}
    assert {"public_id", "tenant_id", "created_at", "updated_at", "deleted_at", "version"} <= columns
    assert model.__mapper__.version_id_col is not None, f"{model.__name__} has no version_id_col"


@pytest.mark.parametrize("model", LINE_ITEM_CHILDREN, ids=lambda m: m.__name__)
def test_line_item_children_are_deliberately_not_audited(model):
    """The other half of Architecture §4's rule. A line has no independent
    lifecycle — it is rewritten with its parent, and the PARENT's version guards
    the edit — so per-line versioning would guard nothing while making every
    parent update a multi-row version dance."""
    assert model.__mapper__.version_id_col is None
    assert "version" not in {c.name for c in model.__table__.columns}


# ------------------------------------------------------------- public ids

def test_public_id_prefixes_match_architecture_section_2():
    actual = {repo.model.__name__: repo.public_id_prefix for repo in ALL_REPOSITORIES}
    for entity, prefix in EXPECTED_PREFIXES.items():
        assert actual[entity] == prefix, f"{entity} should use '{prefix}_', got '{actual[entity]}_'"


def test_public_id_prefixes_are_unique_across_every_repository():
    """A shared prefix is the one bug that turns an id for one entity into a
    valid id for another — `decode_public_id` would resolve it, and someone
    would be shown a row they were never granted."""
    prefixes = [repo.public_id_prefix for repo in ALL_REPOSITORIES]
    assert len(prefixes) == len(set(prefixes)), f"duplicate prefixes: {sorted(prefixes)}"


def test_every_repository_declares_a_model_and_prefix():
    for repo in ALL_REPOSITORIES:
        assert getattr(repo, "model", None) is not None, f"{repo.__name__} has no model"
        assert getattr(repo, "public_id_prefix", None), f"{repo.__name__} has no public_id_prefix"


# ---------------------------------------------------------- money is exact

@pytest.mark.parametrize(
    "model,column",
    [
        (Contract, "wage"),
        (SalaryRule, "amount"),
        (Payslip, "gross_amount"),
        (Payslip, "net_amount"),
        (PayslipLine, "amount"),
    ],
    ids=lambda v: str(v),
)
def test_monetary_columns_are_numeric_never_float(model, column):
    """Architecture §10 — the one rule that cannot slip. A binary float cannot
    represent 0.10 exactly, so amounts summed across a department drift by
    cents that nobody can account for."""
    import sqlalchemy as sa

    col_type = model.__table__.columns[column].type
    assert isinstance(col_type, sa.Numeric), f"{model.__name__}.{column} is {col_type!r}, must be Numeric"
    assert not isinstance(col_type, sa.Float)
    assert col_type.scale == 2


# --------------------------------------------------------------- endpoints

@pytest.mark.parametrize("path,allowed,denied", LIST_ENDPOINTS, ids=lambda v: str(v))
async def test_list_endpoint_allows_its_role_and_returns_a_page(path, allowed, denied):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=_token(allowed))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Real rows through the real service, so an empty list means an empty
    # table — not a stub that will keep returning [] once data exists.
    assert isinstance(body["items"], list)
    assert {"items", "total", "limit", "offset"} <= body.keys()


@pytest.mark.parametrize("path,allowed,denied", LIST_ENDPOINTS, ids=lambda v: str(v))
async def test_list_endpoint_denies_the_wrong_role(path, allowed, denied):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=_token(denied))

    assert resp.status_code == 403, f"{path} let {denied.value} through: {resp.text}"


@pytest.mark.parametrize("path,allowed,denied", LIST_ENDPOINTS, ids=lambda v: str(v))
async def test_admin_reaches_every_list_endpoint(path, allowed, denied):
    """PRD §3: Admin has full access to everything, without any router having
    to remember to name the role."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(path, headers=_token(UserRole.ADMIN))

    assert resp.status_code == 200, resp.text
