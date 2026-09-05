# Feature Flags

## Why this matters
Judges explicitly react well to being able to toggle entire modules live — "watch me disable AI decisions" or "here's what the app looks like with realtime off" is a compact, memorable demonstration of production maturity, and it's one of the cheapest skills in this library to build (a table and a cache, no new infrastructure). It also gives you a genuine safety net: if a feature misbehaves during judging, you flip it off in seconds instead of debugging live.

## Architecture
```
flags table (Postgres) — source of truth, editable via admin UI or API
        │
        ▼
FeatureFlagService — in-memory cache, refreshed on interval + invalidated on write
        │
        ├── Backend: isEnabled(flagKey, context) — gates service-layer logic
        ├── Frontend: useFeatureFlag(flagKey) — gates UI rendering
        └── Admin panel — toggle switches, one per flag, tenant-scoped or global
```
Two evaluation contexts matter: **global** flags (AI decisions on/off for the whole platform) and **tenant-scoped** flags (this specific customer gets the beta export feature). Support both from day one — retrofitting scoping later touches every call site.

## Step 1 — Schema
```prisma
model FeatureFlag {
  id          String   @id @default(uuid())
  key         String   @unique // 'ai_decisions', 'realtime_sync', 'advanced_export'
  description String?
  enabled     Boolean  @default(false)  // global default
  updatedAt   DateTime @updatedAt
  updatedBy   String?

  overrides   FeatureFlagOverride[]
}

model FeatureFlagOverride {
  id        String   @id @default(uuid())
  flagId    String
  tenantId  String
  enabled   Boolean
  flag      FeatureFlag @relation(fields: [flagId], references: [id])

  @@unique([flagId, tenantId])
}
```

## Step 2 — Service with in-memory cache (avoid a DB round-trip on every request)
```js
// src/services/featureFlag.service.js
const prisma = require('../lib/prisma');

let cache = new Map(); // key -> { enabled, overrides: Map(tenantId -> enabled) }
let lastRefresh = 0;
const CACHE_TTL_MS = 10_000; // refresh at most every 10s — flips are near-instant, not truly zero-latency

async function refreshCache() {
  const flags = await prisma.featureFlag.findMany({ include: { overrides: true } });
  const next = new Map();
  flags.forEach(f => {
    next.set(f.key, {
      enabled: f.enabled,
      overrides: new Map(f.overrides.map(o => [o.tenantId, o.enabled])),
    });
  });
  cache = next;
  lastRefresh = Date.now();
}

async function ensureFresh() {
  if (Date.now() - lastRefresh > CACHE_TTL_MS) await refreshCache();
}

async function isEnabled(key, { tenantId } = {}) {
  await ensureFresh();
  const flag = cache.get(key);
  if (!flag) return false; // unknown flags default OFF — fail closed, not open
  if (tenantId && flag.overrides.has(tenantId)) return flag.overrides.get(tenantId);
  return flag.enabled;
}

async function setFlag(key, enabled, updatedBy) {
  await prisma.featureFlag.upsert({
    where: { key },
    update: { enabled, updatedBy },
    create: { key, enabled, updatedBy },
  });
  await refreshCache(); // invalidate immediately on write, don't wait for TTL
}

async function setTenantOverride(key, tenantId, enabled) {
  const flag = await prisma.featureFlag.findUnique({ where: { key } });
  if (!flag) throw new Error(`Unknown flag: ${key}`);
  await prisma.featureFlagOverride.upsert({
    where: { flagId_tenantId: { flagId: flag.id, tenantId } },
    update: { enabled },
    create: { flagId: flag.id, tenantId, enabled },
  });
  await refreshCache();
}

module.exports = { isEnabled, setFlag, setTenantOverride, refreshCache };
```
Call `refreshCache()` once at server boot so the first request isn't served against an empty cache.

## Step 3 — Gating backend logic
```js
// src/ai/bookingDecisionNode.js — replaces the raw env var check from the AI Decision-Node skill
const { isEnabled } = require('../services/featureFlag.service');

async function evaluateBooking(bookingId) {
  const booking = await prisma.booking.findUnique({ where: { id: bookingId } });
  const aiEnabled = await isEnabled('ai_decisions', { tenantId: booking.tenantId });

  if (!aiEnabled) {
    return ruleBasedFallback(bookingId, 'AI decisions disabled via feature flag');
  }
  // ...LLM path as before
}
```
```js
// middleware form, for gating entire routes
function requireFlag(key) {
  return async (req, res, next) => {
    const enabled = await isEnabled(key, { tenantId: req.user?.tenantId });
    if (!enabled) return res.status(404).json({ error: 'Feature not available', code: 'FEATURE_DISABLED' });
    next();
  };
}

router.post('/reports/bookings/advanced', authenticate, requireFlag('advanced_export'), controller.advancedExport);
```
Returning `404` rather than `403` for a disabled feature avoids leaking "this feature exists but you can't use it" to users who shouldn't know about it.

## Step 4 — Admin API to manage flags
```js
// src/routes/featureFlag.routes.js
const router = require('express').Router();
const { setFlag, setTenantOverride } = require('../services/featureFlag.service');
const prisma = require('../lib/prisma');

router.get('/admin/flags', authenticate, authorize('ADMIN'), async (req, res) => {
  const flags = await prisma.featureFlag.findMany({ include: { overrides: true } });
  res.json(flags);
});

router.put('/admin/flags/:key', authenticate, authorize('ADMIN'), async (req, res, next) => {
  try {
    await setFlag(req.params.key, req.body.enabled, req.user.id);
    res.status(204).end();
  } catch (err) { next(err); }
});

router.put('/admin/flags/:key/tenants/:tenantId', authenticate, authorize('ADMIN'), async (req, res, next) => {
  try {
    await setTenantOverride(req.params.key, req.params.tenantId, req.body.enabled);
    res.status(204).end();
  } catch (err) { next(err); }
});

module.exports = router;
```

## Step 5 — Frontend: flag hook + admin toggle panel
```jsx
// src/hooks/useFeatureFlag.js
import { useState, useEffect } from 'react';
import { api } from '../lib/api';

let flagsCache = null;

export function useFeatureFlags() {
  const [flags, setFlags] = useState(flagsCache ?? {});

  useEffect(() => {
    if (flagsCache) return;
    api.get('/me/flags').then(r => { flagsCache = r.data; setFlags(r.data); });
  }, []);

  return flags;
}

export function useFeatureFlag(key) {
  const flags = useFeatureFlags();
  return flags[key] ?? false;
}
```
```js
// backend: resolve all flags relevant to the current user in one call, so the frontend hook is cheap
router.get('/me/flags', authenticate, async (req, res) => {
  const keys = ['ai_decisions', 'realtime_sync', 'advanced_export', 'audit_export'];
  const resolved = {};
  for (const key of keys) resolved[key] = await isEnabled(key, { tenantId: req.user.tenantId });
  res.json(resolved);
});
```
```jsx
// usage: conditionally render a whole module
function App() {
  const aiEnabled = useFeatureFlag('ai_decisions');
  return (
    <>
      <BookingList />
      {aiEnabled && <AIDecisionPanel />}
    </>
  );
}
```
```jsx
// src/pages/AdminFlagsPanel.jsx — the "watch me flip this live" demo surface
function AdminFlagsPanel() {
  const { data: flags, refetch } = useQuery({ queryKey: ['admin-flags'], queryFn: () => api.get('/admin/flags').then(r => r.data) });

  const toggle = async (key, enabled) => {
    await api.put(`/admin/flags/${key}`, { enabled });
    refetch();
  };

  return (
    <div className="space-y-2">
      {flags?.map(f => (
        <div key={f.key} className="flex items-center justify-between p-3 border rounded-md">
          <div>
            <div className="font-medium">{f.key}</div>
            <div className="text-xs text-slate-500">{f.description}</div>
          </div>
          <Switch checked={f.enabled} onChange={(v) => toggle(f.key, v)} />
        </div>
      ))}
    </div>
  );
}
```

## Demo tip
Live-flip `ai_decisions` off in the admin panel, then trigger the AI sweep again on screen and show the rule-based fallback kick in — this is a strong 20-second beat for the "nice to haves" section of your demo script, and it directly demonstrates the flag isn't decorative.

## Enterprise best practices
- **Fail closed**: an unrecognized flag key defaults to `false`/disabled, never assumed enabled — a typo in a flag key should never silently grant access.
- **Cache with short TTL + explicit invalidation on write** — near-instant toggling without a DB round trip on every request.
- Tenant overrides let you dark-launch a feature to one customer/demo tenant without exposing it platform-wide — realistic SaaS pattern.

## Checklist before moving on
- [ ] Cache refreshed at server boot, not just lazily on first request
- [ ] Writing a flag invalidates the cache immediately, not just on the next TTL cycle
- [ ] Unknown flag keys resolve to `false`, never throw or default to `true`
- [ ] Disabled-feature routes return `404`, not a raw crash or a `500`
- [ ] At least one flag (AI decisions is the natural choice) is wired end-to-end and demoable live

## Common mistakes to avoid
- Reading the flags table directly on every request without a cache — defeats the "instant toggle" value prop under any real load and adds unnecessary DB pressure.
- Defaulting unknown flags to `true` — a typo (`ai_decision` vs `ai_decisions`) should fail safe, not silently enable something unintended.
- Hardcoding flag checks as `process.env.X === 'true'` scattered across the codebase — that's what this skill replaces; centralize through `isEnabled()`.

## Integration with the rest of the stack
- Directly replaces the raw `AI_DECISIONS_ENABLED` env var check in the **AI Decision-Node Pattern** skill with a live-toggleable, tenant-aware flag.
- Can gate the **Notification Engine**'s SMS channel, the **Data Export & Reporting Engine**'s advanced formats, or **WebSocket Real-Time Sync** itself if you want a demoable "realtime off" fallback beyond the `REALTIME_MODE` env var.
