# Multi-Tenant Data Isolation Pattern

## Why this matters
Odoo itself is a multi-tenant SaaS ERP — every company using it sees only their own data despite sharing infrastructure. Implementing real tenant isolation (not just a `WHERE companyId = ?` you might forget on one query) signals you understand how enterprise ERP systems are actually architected, and it's a strong differentiator most hackathon teams skip entirely.

## Architecture
```
JWT payload includes tenantId
        │
        ▼
Express middleware sets session-local tenant context
        │
        ▼
PostgreSQL Row-Level Security (RLS) policy
  — enforces tenant scoping AT THE DATABASE LEVEL,
    so even a forgotten WHERE clause cannot leak data
        │
        ▼
Prisma queries run inside a per-request transaction
  that sets the Postgres session variable used by the RLS policy
```
Two layers, same principle as the state machine skill: an application-level check (fast, good errors) plus a database-level guarantee (survives bugs, survives future developers who forget the `WHERE`).

## Step 1 — Schema: tenant_id everywhere
```prisma
model Tenant {
  id        String   @id @default(uuid())
  name      String
  slug      String   @unique
  createdAt DateTime @default(now())
  users     User[]
  bookings  Booking[]
}

model User {
  id        String   @id @default(uuid())
  tenantId  String
  email     String
  role      Role     @default(USER) // USER, TENANT_ADMIN, SUPER_ADMIN
  tenant    Tenant   @relation(fields: [tenantId], references: [id])

  @@unique([tenantId, email]) // email unique PER TENANT, not globally
  @@index([tenantId])
}

model Booking {
  id         String   @id @default(uuid())
  tenantId   String
  resourceId String
  tenant     Tenant   @relation(fields: [tenantId], references: [id])

  @@index([tenantId])
}
```
`SUPER_ADMIN` is a platform-level role (your team, not a customer) that can cross tenants for support purposes — handle it explicitly, never implicitly.

## Step 2 — Row-Level Security policy (the enforcement layer)
```sql
-- prisma/migrations/xxxx_add_rls/migration.sql
ALTER TABLE "Booking" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "User" ENABLE ROW LEVEL SECURITY;

-- app_user is the Postgres role your API connects as (not the Postgres superuser)
CREATE POLICY tenant_isolation_booking ON "Booking"
  USING ("tenantId" = current_setting('app.current_tenant_id')::uuid);

CREATE POLICY tenant_isolation_user ON "User"
  USING ("tenantId" = current_setting('app.current_tenant_id')::uuid);

-- force RLS even for the table owner role (Prisma's migration user), critical in dev
ALTER TABLE "Booking" FORCE ROW LEVEL SECURITY;
ALTER TABLE "User" FORCE ROW LEVEL SECURITY;
```
With RLS enabled, **any** query — even one a teammate writes at 3am without a `WHERE tenantId` clause — is automatically filtered by Postgres itself.

## Step 3 — Setting tenant context per request (Prisma + `$transaction`)
```js
// src/middleware/tenantContext.js
const prisma = require('../lib/prisma');

async function withTenantContext(req, res, next) {
  if (!req.user?.tenantId) return res.status(401).json({ error: 'No tenant context' });

  // Attach a tenant-scoped Prisma client for the rest of this request's handlers
  req.db = {
    run: (fn) => prisma.$transaction(async (tx) => {
      await tx.$executeRawUnsafe(
        `SET LOCAL app.current_tenant_id = '${req.user.tenantId}'`
      );
      return fn(tx);
    }),
  };
  next();
}

module.exports = withTenantContext;
```
`SET LOCAL` scopes the setting to the current transaction only — it can't leak into another concurrent request on a pooled connection, which is the #1 way naive implementations of this pattern break under load.

> Note: `$executeRawUnsafe` is used here only because Postgres does not allow parameter binding on `SET LOCAL`. `req.user.tenantId` is a server-generated UUID from the verified JWT, never raw client input — never interpolate unvalidated strings this way.

## Step 4 — Using it in a service
```js
// src/services/booking.service.js
async function listBookings(req) {
  return req.db.run((tx) => tx.booking.findMany({ orderBy: { createdAt: 'desc' } }));
  // no explicit WHERE tenantId needed — RLS filters it automatically
  // this is the safety net: even if someone forgets, the DB won't leak
}

async function createBooking(req, data) {
  return req.db.run((tx) => tx.booking.create({
    data: { ...data, tenantId: req.user.tenantId }, // still set explicitly on writes
  }));
}
```

## Step 5 — Middleware-based fallback (if RLS isn't available, e.g. managed DB without superuser access)
```js
// src/middleware/tenantScope.js — use ONLY if RLS is unavailable
function tenantScope(req, res, next) {
  req.tenantWhere = { tenantId: req.user.tenantId };
  next();
}

// then EVERY query must manually spread it in — this is the weaker version:
const bookings = await prisma.booking.findMany({ where: { ...req.tenantWhere } });
```
Call out explicitly in your demo that RLS is the production-grade approach and the middleware version is a documented fallback, not the default — this shows judges you understand the tradeoff rather than picked the weaker option by accident.

## Step 6 — JWT tenant propagation
```js
// extends the Role-Based Auth Scaffolder's signAccessToken
function signAccessToken(user) {
  return jwt.sign(
    { sub: user.id, role: user.role, tenantId: user.tenantId, email: user.email },
    process.env.JWT_ACCESS_SECRET,
    { expiresIn: '15m' }
  );
}
```

## Step 7 — Tenant admin vs platform admin handling
```js
// src/middleware/authorize.js extension
function requirePlatformAdmin(req, res, next) {
  if (req.user.role !== 'SUPER_ADMIN') {
    return res.status(403).json({ error: 'Platform admin only' });
  }
  next();
}

// platform admin routes intentionally bypass RLS via a separate, audited path
router.get('/platform/tenants/:id/bookings', authenticate, requirePlatformAdmin, async (req, res) => {
  const bookings = await prisma.booking.findMany({ where: { tenantId: req.params.id } });
  await logAudit(prisma, { entity: 'Tenant', entityId: req.params.id, action: 'CROSS_TENANT_VIEW', actorId: req.user.id });
  res.json(bookings);
});
```
Any cross-tenant access must be its own explicit, audited route — never a query that "happens to" ignore tenant scope.

## Frontend considerations
- Tenant is never selectable by the user in a request payload — it always comes from the authenticated session, never `req.body.tenantId`.
- Subdomain-per-tenant (`acme.yourapp.com`) is a common enterprise pattern if time allows, but a simple "Workspace: Acme Corp" indicator in the topbar is sufficient for a hackathon demo.

## Testing tenant isolation
```js
// test/tenant-isolation.test.js
it('cannot read another tenant\'s bookings even with a valid JWT', async () => {
  const tenantAToken = await loginAs(tenantAUser);
  const res = await request(app)
    .get(`/bookings/${tenantBBookingId}`)
    .set('Authorization', `Bearer ${tenantAToken}`);
  expect(res.status).toBe(404); // RLS makes it invisible, not just forbidden
});
```
Getting a `404` (not `403`) is the correct outcome — from Tenant A's perspective, Tenant B's row should not appear to exist at all.

## Why this mirrors real SaaS ERP systems
Odoo, Salesforce, and every serious B2B SaaS platform isolate customer data this way — shared infrastructure, hard per-tenant boundaries enforced below the application layer so no application bug can cause a cross-customer data leak. Demonstrating this pattern, even simplified, shows judges you understand what "enterprise-grade" actually requires.

## Checklist before moving on
- [ ] Every tenant-scoped table has RLS enabled and `FORCE`d
- [ ] `tenantId` comes from the JWT, never from the request body
- [ ] `SET LOCAL` used (not `SET`) so context can't leak across pooled connections
- [ ] Cross-tenant admin routes are separate, explicit, and audited
- [ ] A test proves cross-tenant reads return 404, not empty-but-200

## Common mistakes to avoid
- Using `SET` instead of `SET LOCAL` — leaks tenant context to the next request on a reused pooled connection.
- Trusting a `tenantId` sent in the request body on write operations.
- Forgetting to `FORCE ROW LEVEL SECURITY` — without it, the table owner role (often your migration user) bypasses RLS entirely.

## Integration with the rest of the stack
- Extends the **Role-Based Auth Scaffolder**'s JWT payload and middleware chain directly.
- The **Immutable Audit Log** skill should also carry `tenantId` on every entry for the same isolation guarantee.
