"""Concurrency proof for app/core/locks.py.

Runs two real transactions against the real Postgres at the same time — an
advisory lock is a property of the database's lock manager, so a mocked session
would prove nothing about it.

Each test asserts on the ORDER of observed events, not on elapsed time, so a
slow machine makes the test slower rather than flaky. HOLD_SECONDS only has to
be long enough that the "did not wait" case is unambiguous.
"""
import asyncio
import time

import pytest
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.core.locks import acquire_entity_lock, entity_lock_key

# How long the first transaction sits on the lock before committing.
HOLD_SECONDS = 0.75

ENTITY_TYPE = "locktest"


async def _hold_lock(entity_id, holder_has_lock: asyncio.Event, log: list):
    """Take the lock, signal that it is held, keep it for HOLD_SECONDS, commit."""
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await acquire_entity_lock(session, ENTITY_TYPE, entity_id)
            log.append(("holder_acquired", time.monotonic()))
            holder_has_lock.set()
            await asyncio.sleep(HOLD_SECONDS)
            log.append(("holder_about_to_commit", time.monotonic()))
        # COMMIT has happened here; the advisory lock is released.
        log.append(("holder_committed", time.monotonic()))


async def _contend_for_lock(entity_id, holder_has_lock: asyncio.Event, log: list):
    """Wait until the holder owns its lock, then try to take a lock ourselves."""
    await holder_has_lock.wait()
    async with AsyncSessionLocal() as session:
        async with session.begin():
            log.append(("contender_requesting", time.monotonic()))
            await acquire_entity_lock(session, ENTITY_TYPE, entity_id)
            log.append(("contender_acquired", time.monotonic()))


def _at(log, event):
    return next(ts for name, ts in log if name == event)


def _print_timeline(title: str, log: list) -> None:
    """Show the observed ordering. Visible with `pytest -s`; this is the raw
    evidence behind the assertions below."""
    t0 = min(ts for _, ts in log)
    print(f"\n{title}")
    for name, ts in sorted(log, key=lambda e: e[1]):
        print(f"  t+{ts - t0:6.3f}s  {name}")


async def test_same_entity_serializes():
    """Two transactions locking the SAME entity must not overlap: the second
    acquires only after the first commits."""
    log: list = []
    holder_has_lock = asyncio.Event()

    await asyncio.gather(
        _hold_lock(1, holder_has_lock, log),
        _contend_for_lock(1, holder_has_lock, log),
    )

    _print_timeline("SAME entity (both locking locktest_1) — contender must wait:", log)

    names = [name for name, _ in log]
    assert "contender_acquired" in names, log

    # The contender asked for the lock while the holder still had it...
    assert _at(log, "contender_requesting") < _at(log, "holder_about_to_commit"), log
    # ...and did not get it until the holder was done with it.
    #
    # Compared against `holder_about_to_commit`, NOT `holder_committed`. Both
    # coroutines share one event loop, and `holder_committed` is appended only
    # when the loop resumes the holder AFTER its COMMIT round-trip returns. The
    # database releases the advisory lock at COMMIT, so the contender can
    # legitimately unblock and append its own entry in between — this assertion
    # used to compare against `holder_committed` and failed roughly one run in
    # three on a 0.6ms inversion that proved nothing about the lock.
    #
    # `holder_about_to_commit` is logged INSIDE the transaction, while the lock
    # is still held, so acquiring at or after it is the real property.
    assert _at(log, "contender_acquired") >= _at(log, "holder_about_to_commit"), log
    # And it genuinely blocked rather than merely being scheduled late: the
    # wait covers the whole hold. This is the assertion the ordering one was
    # trying to make, expressed so that scheduling order cannot fake it.
    waited = _at(log, "contender_acquired") - _at(log, "contender_requesting")
    assert waited >= HOLD_SECONDS * 0.9, (waited, log)


async def test_different_entities_do_not_serialize():
    """A lock on one entity must not block a lock on a different entity —
    otherwise this would be a global lock wearing a per-entity disguise."""
    log: list = []
    holder_has_lock = asyncio.Event()

    await asyncio.gather(
        _hold_lock(1, holder_has_lock, log),
        _contend_for_lock(2, holder_has_lock, log),
    )

    _print_timeline(
        "DIFFERENT entities (locktest_1 vs locktest_2) — contender must NOT wait:", log
    )

    # Acquired while entity 1's holder was still inside its transaction.
    assert _at(log, "contender_acquired") < _at(log, "holder_about_to_commit"), log


async def test_lock_is_released_by_rollback():
    """Transaction scope means ROLLBACK releases the lock too — a failed
    operation must not leave the entity permanently locked."""
    async with AsyncSessionLocal() as session:
        await session.begin()
        await acquire_entity_lock(session, ENTITY_TYPE, 3)
        await session.rollback()

    # A second transaction can now take it without waiting.
    async with AsyncSessionLocal() as other:
        async with other.begin():
            got = (await other.execute(
                text("SELECT pg_try_advisory_xact_lock(hashtext(:k))"),
                {"k": entity_lock_key(ENTITY_TYPE, 3)},
            )).scalar_one()

    assert got is True, "lock survived ROLLBACK — it is not transaction-scoped"


@pytest.mark.parametrize(
    ("entity_type", "entity_id", "expected"),
    [
        ("trip", 7, "trip_7"),
        ("trip", "bkg_x9", "trip_bkg_x9"),
        ("order", 7, "order_7"),
    ],
)
def test_lock_key_namespacing(entity_type, entity_id, expected):
    """Different entity types with the same id must not share a lock key."""
    assert entity_lock_key(entity_type, entity_id) == expected
