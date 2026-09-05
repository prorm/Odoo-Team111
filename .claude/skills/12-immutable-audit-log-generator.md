# Immutable Audit Log Generator

## Why this matters
Real ERP systems are auditable by design — every state change traceable to who (or what) did it and why. An append-only log that cannot be edited or deleted, distinguishing human/system/AI actors, is a concrete enterprise pattern that's cheap to build and pays off across every other skill in this library (state transitions, AI decisions, tenant admin actions all write here).

## Architecture
```
Any mutating service call (booking transition, tenant admin action, AI decision)
        │
        ▼
logAudit(tx, { entity, entityId, action, actorId, actorType, metadata })
        │
        ▼
audit_log table — INSERT only
  ├── No UPDATE route exists in the API
  ├── No DELETE route exists in the API
  ├── Database-level guard (trigger) rejects any UPDATE/DELETE even from a bug or a rogue admin query
        │
        ▼
Reconstruction queries — "show me this entity's full history"
```
The guarantee that matters to judges: even a bug in your own code, or someone with raw DB access, cannot silently rewrite history — the trigger enforces it below the application layer.

## Step 1 — Schema
```prisma
model AuditLog {
  id         String   @id @default(uuid())
  tenantId   String?
  entity     String   // 'Booking', 'User', 'Tenant', etc.
  entityId   String
  action     String   // 'CREATE', 'TRANSITION_CONFIRMED', 'AI_DECISION_CANCELLED', etc.
  actorId    String?  // null for system/AI-initiated actions with no human actor
  actorType  String   // 'human' | 'system' | 'agent'
  metadata   Json?    // reasoning, confidence, before/after values, etc.
  createdAt  DateTime @default(now())

  @@index([entity, entityId, createdAt])
  @@index([tenantId, createdAt])
  @@index([actorId])
}
```

## Step 2 — Database-level immutability guard (the load-bearing part)
```sql
-- prisma/migrations/xxxx_add_audit_immutability/migration.sql
CREATE OR REPLACE FUNCTION prevent_audit_mutation()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_log_no_update
  BEFORE UPDATE ON "AuditLog"
  FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();

CREATE TRIGGER audit_log_no_delete
  BEFORE DELETE ON "AuditLog"
  FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();
```
This is the artifact to show a judge who asks "how do you know your audit log wasn't tampered with?" — it's not a code convention, the database itself refuses the operation.

## Step 3 — The logging function
```js
// src/services/audit.service.js
async function logAudit(tx, { entity, entityId, action, actorId = null, actorType = 'human', tenantId = null, metadata = null }) {
  return tx.auditLog.create({
    data: { entity, entityId, action, actorId, actorType, tenantId, metadata },
  });
}

module.exports = { logAudit };
```
Always accept `tx` (the current Prisma transaction) rather than a fresh client — the audit entry must commit atomically with the state change it's recording, or a crash between the two leaves an inconsistent trail.

```js
// usage across the stack — human actor
await logAudit(tx, { entity: 'Booking', entityId: booking.id, action: 'CREATE', actorId: req.user.id, actorType: 'human', tenantId: req.user.tenantId });

// AI actor (from the AI Decision-Node skill)
await logAudit(tx, { entity: 'Booking', entityId: bookingId, action: 'AI_DECISION_CANCELLED', actorId: null, actorType: 'agent', metadata: { reasoning, confidence } });

// system actor (e.g. a scheduled cleanup job)
await logAudit(tx, { entity: 'Booking', entityId: bookingId, action: 'AUTO_EXPIRED', actorId: null, actorType: 'system' });
```

## Step 4 — No UPDATE/DELETE routes exist by design
```js
// src/routes/audit.routes.js — READ ONLY, intentionally
const router = require('express').Router();
router.get('/audit/:entity/:entityId', authenticate, authorize('ADMIN', 'TENANT_ADMIN'), auditController.history);
router.get('/audit/actor/:actorId', authenticate, authorize('ADMIN'), auditController.byActor);
// No PATCH, no DELETE — there is nothing here to wire up, by design
module.exports = router;
```

## Step 5 — Entity history reconstruction
```js
// src/controllers/audit.controller.js
async function history(req, res, next) {
  try {
    const entries = await prisma.auditLog.findMany({
      where: { entity: req.params.entity, entityId: req.params.entityId },
      orderBy: { createdAt: 'asc' },
    });
    res.json(entries.map(formatEntry));
  } catch (err) { next(err); }
}

function formatEntry(e) {
  return {
    action: e.action,
    actor: e.actorType === 'human' ? { type: 'human', id: e.actorId } : { type: e.actorType },
    at: e.createdAt,
    metadata: e.metadata,
  };
}

async function byActor(req, res, next) {
  try {
    const entries = await prisma.auditLog.findMany({
      where: { actorId: req.params.actorId },
      orderBy: { createdAt: 'desc' },
      take: 100,
    });
    res.json(entries);
  } catch (err) { next(err); }
}

module.exports = { history, byActor };
```

## Step 6 — Example queries
```sql
-- Full lifecycle of one booking, in order
SELECT action, "actorType", "actorId", metadata, "createdAt"
FROM "AuditLog"
WHERE entity = 'Booking' AND "entityId" = 'xxxx-xxxx'
ORDER BY "createdAt" ASC;

-- Every AI-driven decision in a tenant, for a "how much did the AI do" demo stat
SELECT COUNT(*), action
FROM "AuditLog"
WHERE "tenantId" = 'xxxx' AND "actorType" = 'agent'
GROUP BY action;

-- Who cancelled the most bookings (accountability query)
SELECT "actorId", COUNT(*) FROM "AuditLog"
WHERE entity = 'Booking' AND action LIKE 'TRANSITION_CANCELLED%'
GROUP BY "actorId" ORDER BY COUNT(*) DESC;
```

## Indexing strategy
- `(entity, entityId, createdAt)` — the primary access pattern, one entity's full timeline
- `(tenantId, createdAt)` — tenant-wide activity feeds/dashboards
- `(actorId)` — "what has this user/admin done" queries
These three cover essentially every reporting and audit-trail query you'll need without a full table scan.

## Frontend: history timeline component
```jsx
function AuditTimeline({ entity, entityId }) {
  const { status, data } = useAsync(() => api.get(`/audit/${entity}/${entityId}`).then(r => r.data), [entityId]);
  return (
    <AsyncBoundary status={status} isEmpty={!data?.length}>
      <ol className="border-l-2 border-slate-200 pl-4 space-y-4">
        {data?.map((e, i) => (
          <li key={i}>
            <div className="text-sm font-medium">{e.action}</div>
            <div className="text-xs text-slate-500">
              by {e.actor.type === 'human' ? `user ${e.actor.id?.slice(0,8)}` : e.actor.type} · {new Date(e.at).toLocaleString()}
            </div>
            {e.metadata?.reasoning && <div className="text-xs italic text-slate-400 mt-1">"{e.metadata.reasoning}"</div>}
          </li>
        ))}
      </ol>
    </AsyncBoundary>
  );
}
```

## Checklist before moving on
- [ ] Trigger installed and manually verified: an `UPDATE`/`DELETE` on `AuditLog` throws
- [ ] Every state-changing service function writes an audit entry inside the same transaction
- [ ] `actorType` discriminates human/system/agent consistently across all call sites
- [ ] No route in the codebase modifies or deletes an audit entry
- [ ] Indexes match your actual query patterns (entity lookup, tenant feed, actor lookup)

## Common mistakes to avoid
- Writing the audit entry in a separate, non-transactional call after the main write — a crash in between leaves a state change with no corresponding record.
- Forgetting the DB trigger and relying on "we just won't build an update route" — that's a convention, not a guarantee; the trigger is what makes it actually immutable.
- Storing free-text `metadata` without structure — keep it a consistent JSON shape per action type so reconstruction queries stay simple.

## Integration with the rest of the stack
- Called from **State Machine + Constraint Generator** on every transition, **AI Decision-Node Pattern** on every agent decision, and **Multi-Tenant Data Isolation**'s cross-tenant admin routes.
- `tenantId` on each entry lets tenant admins see only their own audit trail, consistent with the RLS pattern.
