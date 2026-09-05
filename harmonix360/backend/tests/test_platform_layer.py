"""Platform-layer tripwire suite.

Deliberately small. It is not coverage — it is a tripwire for the handful of
framework behaviours PeoplePay360 leans on hardest, so that a break in the
foundation fails CI instead of failing silently on demo day.

Its second job is the one Phase 0 specifically needs: proving the retained
Platform/Intelligence layer (audit, idempotency, offline sync, AI, MCP,
observability) still works with ZERO domain entities registered, after the
AssetFlow domain it was originally wired to was deleted. If any of these fail,
the "keep the infra, swap the domain" claim is false.
"""
import uuid

import pytest
from sqlalchemy import delete, text

from app.audit.logger import AuditLogger
from app.core.redis import redis_client
from app.middleware.idempotency import cache_key
from app.models.department import Department
from app.repositories.audit_query import AuditQueryRepository
from app.repositories.base import BaseRepository
from app.services.sync_registry import registered_entity_types


class _DepartmentRepository(BaseRepository[Department]):
    """Department is not an AuditedEntity, but it carries the same
    id/public_id/tenant_id/deleted_at columns BaseRepository's read paths use,
    which is all this round-trip exercises."""

    model = Department
    public_id_prefix = "dept"


def _code() -> str:
    return f"PLT-{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------- app boots

async def test_health_endpoint_returns_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_openapi_schema_builds(client):
    """Every mounted router still resolves its response models.

    A dangling import or a schema referencing a deleted model surfaces here as
    a failure, rather than at the first request to that one endpoint.
    """
    resp = await client.get("/api/v1/openapi.json")
    assert resp.status_code == 200
    assert "paths" in resp.json()


# ------------------------------------------------- repository scaffolding

async def test_base_repository_round_trip(session):
    """create() assigns a prefixed public_id that get_by_public_id resolves back
    to the same row — the generic scaffolding every HR entity inherits."""
    repo = _DepartmentRepository(session)
    created = await repo.create(Department(public_id="temp", name="Round Trip", code=_code()))
    await session.commit()

    try:
        assert created.public_id.startswith("dept_")
        fetched = await repo.get_by_public_id(created.public_id)
        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.name == "Round Trip"
    finally:
        await session.execute(delete(Department).where(Department.id == created.id))
        await session.commit()


async def test_public_id_prefix_prevents_cross_entity_confusion(session):
    """A public_id minted for one entity must not decode under another prefix.

    One shared hashids salt encodes each table's own autoincrement sequence, so
    Employee id=5 and Payslip id=5 hash to the identical string; only the
    prefix keeps `pslip_xxx` from resolving to a real (wrong) Employee row.
    """
    from app.core.security import decode_public_id

    repo = _DepartmentRepository(session)
    created = await repo.create(Department(public_id="temp", name="Prefix Check", code=_code()))
    await session.commit()

    try:
        assert decode_public_id(created.public_id, "dept") == created.id
        assert decode_public_id(created.public_id, "emp") is None
    finally:
        await session.execute(delete(Department).where(Department.id == created.id))
        await session.commit()


# ------------------------------------------------------------------ audit

async def test_audit_log_write_and_query_round_trip(session):
    """The append-only audit log still writes and reads back with no domain
    entity involved — AuditLogger takes `entity` as a plain string, which is
    exactly why deleting a whole domain never touched it."""
    entity_id = f"emp_audittest_{uuid.uuid4().hex[:8]}"

    await AuditLogger.log_mutation(
        session=session,
        actor="tests@peoplepay360.com",
        action="CREATE_EMPLOYEE",
        entity="Employee",
        entity_id=entity_id,
        after_diff={"status": "active"},
        reason="platform-layer tripwire",
    )
    await session.commit()

    entries = await AuditQueryRepository(session).get_by_entity_id(entity_id, limit=10)
    assert len(entries) == 1
    assert entries[0].actor == "tests@peoplepay360.com"
    assert entries[0].action == "CREATE_EMPLOYEE"
    assert entries[0].after_diff == {"status": "active"}

    # Deliberately no cleanup: DELETE on audit_logs is revoked from the runtime
    # role, which is the whole point of the table. The row is scoped by a
    # random entity_id so repeated runs cannot collide.


async def test_audit_log_is_append_only_for_the_runtime_role(session):
    """UPDATE/DELETE on audit_logs are revoked from harmonix360_app in
    migration 001, so immutability is a database grant rather than a code
    convention. Skipped when the suite happens to connect as a superuser, for
    whom the revoke does not apply."""
    current_role = (await session.execute(text("SELECT current_user"))).scalar()
    if current_role != "harmonix360_app":
        pytest.skip(f"connected as {current_role!r}; the REVOKE only binds harmonix360_app")

    entity_id = f"emp_immutable_{uuid.uuid4().hex[:8]}"
    await AuditLogger.log_mutation(
        session=session, actor="tests", action="X", entity="Employee", entity_id=entity_id
    )
    await session.commit()

    with pytest.raises(Exception):
        await session.execute(
            text("UPDATE audit_logs SET actor = 'tampered' WHERE entity_id = :eid"), {"eid": entity_id}
        )
        await session.commit()
    await session.rollback()


# ------------------------------------------------------------ offline sync

def test_sync_registry_holds_exactly_the_two_phase_8_entities():
    """Offline sync is opt-in per entity by registry membership.

    Phase 8 registers exactly `attendance` and `time_off_request`
    (Architecture §8.3). This asserts the WHOLE set, not membership, because
    the risk this test exists for is an entity appearing that nobody decided
    to expose — a payroll entity above all. Adding one has to break a test
    and be argued for, not slip in.
    """
    assert sorted(registered_entity_types()) == ["attendance", "time_off_request"]

    forbidden = {
        "payrun", "payslip", "payslip_line", "salary_rule", "salary_structure",
        "contract", "employee", "time_off_allocation", "time_off_type", "user",
    }
    assert forbidden.isdisjoint(registered_entity_types())


async def test_sync_pull_rejects_unregistered_entity_type(client):
    """With an empty registry the engine is still reachable and correctly
    refuses every entity type, rather than 500-ing on the deleted Note/Asset
    imports it used to carry."""
    resp = await client.get("/api/v1/sync/pull", params={"entity_types": "note"})
    assert resp.status_code == 400, resp.text
    assert "Unknown entity_types" in resp.json()["detail"]


async def test_sync_push_does_not_apply_an_unregistered_entity_type(client):
    resp = await client.post(
        "/api/v1/sync/push",
        json={
            "mutations": [
                {
                    "client_mutation_id": f"cm_{uuid.uuid4().hex[:12]}",
                    "entity_type": "asset",
                    "op": "CREATE",
                    "payload": {"name": "should not apply"},
                }
            ]
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["results"][0]["outcome"] != "applied"


# ------------------------------------------------------------ idempotency

async def test_idempotency_key_replays_the_cached_response(client):
    """IdempotencyMiddleware caches any 2xx POST response by header value.

    Exercised against /auth/login because it was, when this test was written,
    the only POST the app exposed — the middleware itself is entity-blind,
    which is the property under test. Phase 4 scoped the cache entry to
    (method, path, key) so a key reused across two DIFFERENT endpoints cannot
    answer one with the other's response; replaying the SAME request, tested
    here, is unchanged.
    """
    key = f"test-{uuid.uuid4().hex}"
    body = {"email": "hr.manager@peoplepay360.com", "password": "hrmanager123"}

    first = await client.post("/api/v1/auth/login", json=body, headers={"Idempotency-Key": key})
    assert first.status_code == 200, first.text

    second = await client.post("/api/v1/auth/login", json=body, headers={"Idempotency-Key": key})
    assert second.status_code == 200
    assert second.json() == first.json()

    await redis_client.delete(cache_key("POST", "/api/v1/auth/login", key))


# -------------------------------------------- platform layer still imports

def test_platform_layer_modules_import_with_no_domain_entities():
    """The AI, MCP, realtime and observability modules are kept dormant, not
    deleted. Importing them is the cheapest proof that stripping the AssetFlow
    domain out from under them left them intact."""
    import app.ai.cache  # noqa: F401
    import app.ai.decision_nodes  # noqa: F401
    import app.ai.provider_router  # noqa: F401
    import app.ai.review_job  # noqa: F401
    import app.core.telemetry  # noqa: F401
    import app.jobs.tasks.ai_jobs  # noqa: F401
    import app.mcp.server as mcp_server
    import app.realtime.ws_manager as ws_manager

    # The MCP server keeps its FastMCP instance and its API-key auth model;
    # only the AssetFlow tool set was removed.
    assert mcp_server.mcp is not None
    assert ws_manager.manager.channel_size("payroll") == 0


# ---------------------------------------------------------- Department

def test_department_carries_no_domain_specific_columns():
    """Department is reused as-is by PeoplePay360 (Architecture §4, "reuse
    existing model as-is"), so it must stay a plain organisational unit.

    The exact column list is asserted rather than a "no column named asset_*"
    heuristic, because the failure this guards against is someone hanging a
    domain-specific field off the one table three domains share — and that
    field will not necessarily be called anything obvious. When PeoplePay360
    legitimately needs a new Department column, updating this list is the
    deliberate step that makes it a decision rather than a drift.
    """
    assert {c.name for c in Department.__table__.columns} == {
        "id",
        "public_id",
        "name",
        "code",
        "head_id",
        "status",
        "tenant_id",
        "created_at",
        "updated_at",
        "deleted_at",
    }
    # The only FK is the department head, into users — nothing AssetFlow-shaped.
    assert {str(fk.target_fullname) for c in Department.__table__.columns for fk in c.foreign_keys} == {"users.id"}
