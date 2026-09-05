"""Transaction-scoped Postgres advisory locks, keyed by entity.

Why this exists
---------------
Postgres constraints protect a *row*. They cannot protect an *invariant that
spans several rows of the same parent* — "the stops of this trip are numbered
1..n with no gaps", "the line items of this order sum to the order total". An
operation that rewrites a whole parent-scoped collection reads the current set,
computes a new arrangement, and writes it back; two of those running
concurrently on the same parent interleave their read and write phases and
produce a set that neither one intended, with every individual row still
perfectly valid.

`pg_advisory_xact_lock` serializes those operations by agreement: every writer
of a given parent's collection takes the same lock first, so the read-compute-
write window is never entered by two transactions at once. The lock is held for
the remainder of the transaction and released by COMMIT or ROLLBACK — there is
no unlock call to forget, and a crashed session cannot leave it stuck.

What it does NOT do
-------------------
This is cooperative. It only works if every writer of that collection calls it;
a writer that skips it is not blocked. It is also not a substitute for a
constraint — reach for a constraint whenever the invariant fits in one row, and
use this only for the genuinely multi-row case.

Usage
-----
Call it at the START of the transaction, before reading anything you intend to
rewrite. Taking it after the read defeats the purpose: the stale read has
already happened.

    async with AsyncSessionLocal() as session:
        async with session.begin():
            await acquire_entity_lock(session, "trip", trip_id)
            ...read the collection, compute new ordering, write it back...
        # COMMIT releases the lock

Harmonix360 core has no bulk-resequencing operation of its own today (nothing in
the AssetFlow domain stores a user-controlled sort order), so there is no call
site here yet. It ships as core infrastructure because the alternative — a fork
inventing its own locking under deadline pressure the first time it adds an
ordered child collection — is how the read-modify-write bug gets written. See
HARMONIX360_ARCHITECTURE.md Section 2a.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# hashtext() maps the key to the int4 that pg_advisory_xact_lock() wants.
# Bound parameter, never interpolation: the key contains caller-supplied data.
_ACQUIRE_SQL = text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))")


def entity_lock_key(entity_type: str, entity_id: int | str) -> str:
    """The string hashed into the lock id: ``"{entity_type}_{entity_id}"``.

    Exposed so tests and debugging can name the exact lock a transaction holds
    without duplicating the format string.
    """
    return f"{entity_type}_{entity_id}"


async def acquire_entity_lock(
    session: AsyncSession, entity_type: str, entity_id: int | str
) -> None:
    """Take the transaction-scoped advisory lock for one entity, and wait if
    another transaction already holds it.

    Args:
        session: an AsyncSession with a transaction already open. The lock binds
            to that transaction and is released automatically on COMMIT/ROLLBACK.
        entity_type: the parent collection's namespace, e.g. "trip", "order".
            Keeps unrelated domains from sharing a lock id by coincidence.
        entity_id: the parent's id. Internal integer id or public_id string —
            just be consistent, since "trip_7" and "trip_bkg_x9" are different
            locks even when they name the same row.

    Returns when the lock is held. Blocks indefinitely otherwise: there is no
    timeout, because the operations this guards are short and a caller that
    would rather fail than wait wants `pg_try_advisory_xact_lock`, which is a
    different contract (returns false instead of waiting) and would need its own
    helper.

    Two entity keys can collide: hashtext maps into 32 bits, so distinct keys
    can hash to the same lock id. The failure mode is one-directional — two
    unrelated entities occasionally serialize against each other, costing a
    little throughput. It never lets two writers of the SAME entity through
    together, which is the property that matters.
    """
    await session.execute(_ACQUIRE_SQL, {"lock_key": entity_lock_key(entity_type, entity_id)})
