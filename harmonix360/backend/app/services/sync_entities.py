"""Registers the entity types this PeoplePay360 deployment exposes through
/sync/pull and /sync/push. Imported for its registration side effect only
(app/services/sync.py does `from app.services import sync_entities  # noqa: F401`).

Currently registers ZERO entities, on purpose.

The offline-sync engine (app/services/sync_registry.py, app/services/sync.py,
app/api/v1/routers/sync.py, the SyncMutation idempotency table, and the
frontend's IndexedDB outbox) is fully retained — it is Platform/Intelligence
layer infrastructure, not domain content. What it used to sync was:
`note` and `asset`, AssetFlow's proof-of-concept entities, which were deleted
in Phase 0 step 1 as wrong-domain. Their registrations went with them.

PHASE 8 registers the real targets here, per Architecture §8.3:

    attendance          — check-in / check-out only. Attendance CORRECTIONS
                          stay online-only and role-gated; an offline device
                          may record that someone arrived, never rewrite what
                          was already recorded.
    time_off_request    — create only.

Nothing else is ever registered. Payroll, Salary Rule, Contract and every other
core entity are deliberately excluded: offline capability is opt-in by registry
membership, and payroll mutations are never opted in (Architecture §8.3).

Registering an entity is one `register_syncable_entity(...)` call in this file
and nothing else — app/services/sync.py and app/api/v1/routers/sync.py do not
change. With the registry empty, /sync/pull rejects every entity_types value
with a 400 that lists the (empty) registered set, and /sync/push reports each
mutation as an unknown entity type. That is the correct dormant behaviour: the
engine is present and tested, with no domain surface exposed yet.
"""

# Intentionally empty until Phase 8. See the module docstring.
