# Graceful Rate Limiting & System Throttling

## Why this matters
"Production-ready" implies your API survives abuse, not just happy-path demo traffic. Rate limiting is cheap to add, easy to demo (trigger it live), and directly answers the judge question "what stops someone from hammering your login endpoint?" It's one of the highest-ROI production-readiness signals available in the time you have.

## Architecture
```
Request
  │
  ▼
express-rate-limit middleware
  │
  ├── Redis store (shared across instances, survives restarts) ─ preferred
  └── in-memory store (fallback if Redis unavailable)            ─ dev/demo fallback
  │
  ▼
Per-route limit config (auth stricter than reads)
  │
  ▼
429 response with Retry-After + standard rate-limit headers
```
Different endpoints need different limits: `/auth/login` needs aggressive throttling (brute-force target), `/bookings` (read) can be generous, `/bookings` (write) moderate.

## Step 1 — Install and configure the Redis-backed limiter
```bash
npm install express-rate-limit rate-limit-redis ioredis
```
```js
// src/lib/redis.js
const Redis = require('ioredis');

const redis = new Redis(process.env.REDIS_URL || 'redis://localhost:6379', {
  maxRetriesPerRequest: 1,
  enableOfflineQueue: false,
});

redis.on('error', (err) => console.warn('[redis] connection issue, rate limiting will fall back to memory:', err.message));

module.exports = redis;
```
```js
// src/middleware/rateLimiter.js
const rateLimit = require('express-rate-limit');
const { RedisStore } = require('rate-limit-redis');
const redis = require('../lib/redis');

function buildLimiter({ windowMs, max, keyPrefix, message }) {
  const useRedis = redis.status === 'ready' || redis.status === 'connecting';

  return rateLimit({
    windowMs,
    max,
    standardHeaders: true,   // adds RateLimit-* headers
    legacyHeaders: false,
    message: { error: message, code: 'RATE_LIMIT_EXCEEDED' },
    store: useRedis ? new RedisStore({
      sendCommand: (...args) => redis.call(...args),
      prefix: `rl:${keyPrefix}:`,
    }) : undefined, // falls back to express-rate-limit's built-in in-memory store
    keyGenerator: (req) => req.user?.id || req.ip, // auth-aware: per-user if logged in, per-IP otherwise
    handler: (req, res, _next, options) => {
      console.warn(`[rate-limit] ${keyPrefix} exceeded by ${req.user?.id || req.ip} on ${req.path}`);
      res.status(429).json(options.message);
    },
  });
}

module.exports = { buildLimiter };
```

## Step 2 — Endpoint-specific limits
```js
// src/middleware/limits.js
const { buildLimiter } = require('./rateLimiter');

const authLimiter = buildLimiter({
  windowMs: 60 * 1000,       // 1 minute
  max: 5,                    // 5 login attempts per minute per IP
  keyPrefix: 'auth',
  message: 'Too many login attempts. Please try again in a minute.',
});

const writeLimiter = buildLimiter({
  windowMs: 60 * 1000,
  max: 30,                   // 30 writes per minute per user
  keyPrefix: 'write',
  message: 'You are performing actions too quickly. Please slow down.',
});

const readLimiter = buildLimiter({
  windowMs: 60 * 1000,
  max: 300,                  // generous, reads are cheap
  keyPrefix: 'read',
  message: 'Too many requests. Please slow down.',
});

module.exports = { authLimiter, writeLimiter, readLimiter };
```

## Step 3 — Wiring into routes
```js
const { authLimiter, writeLimiter, readLimiter } = require('../middleware/limits');

router.post('/auth/login', authLimiter, authController.login);
router.get('/bookings', readLimiter, authenticate, bookingController.list);
router.post('/bookings', writeLimiter, authenticate, bookingController.create);
```
Apply the auth limiter **before** authentication middleware (it must key on IP since there's no `req.user` yet); apply write/read limiters after, so they can key on `req.user.id`.

## Step 4 — Standard 429 response with retry headers
`express-rate-limit` with `standardHeaders: true` automatically sets:
```
RateLimit-Limit: 30
RateLimit-Remaining: 0
RateLimit-Reset: 42
Retry-After: 42
```
Client-side, surface this directly instead of a generic error:
```jsx
// src/lib/api.js — axios interceptor
api.interceptors.response.use(null, (error) => {
  if (error.response?.status === 429) {
    const retryAfter = error.response.headers['retry-after'];
    toast(`Slow down — try again in ${retryAfter}s`, 'error');
  }
  return Promise.reject(error);
});
```

## Step 5 — Abuse-prevention pattern beyond simple counting
```js
// src/middleware/progressiveAuthLimiter.js
// Escalates lockout duration on repeated failures, not just a flat window
const redis = require('../lib/redis');

async function progressiveLockoutCheck(req, res, next) {
  const key = `lockout:${req.body.email}`;
  const failures = parseInt(await redis.get(key)) || 0;

  if (failures >= 10) {
    return res.status(429).json({
      error: 'Account temporarily locked due to repeated failed attempts. Try again in 15 minutes.',
      code: 'ACCOUNT_LOCKED',
    });
  }
  next();
}

async function recordFailedLogin(email) {
  const key = `lockout:${email}`;
  const failures = await redis.incr(key);
  if (failures === 1) await redis.expire(key, 15 * 60); // 15 min window
}

async function clearFailedLogins(email) {
  await redis.del(`lockout:${email}`);
}

module.exports = { progressiveLockoutCheck, recordFailedLogin, clearFailedLogins };
```
```js
// wired into login controller
router.post('/auth/login', authLimiter, progressiveLockoutCheck, async (req, res, next) => {
  try {
    const result = await authService.login(req.body);
    await clearFailedLogins(req.body.email);
    res.json(result);
  } catch (err) {
    if (err.statusCode === 401) await recordFailedLogin(req.body.email);
    next(err);
  }
});
```

## Demo script showing intentional throttling
> "Let's demonstrate abuse protection." Open a terminal, fire 6 rapid login attempts with a wrong password via `curl` or a small script:
```bash
for i in {1..6}; do curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:4000/auth/login \
  -H "Content-Type: application/json" -d '{"email":"demo@test.com","password":"wrong"}'; done
```
Show the sequence of `401 401 401 401 401 429` on screen — the 6th request is blocked with a clean rate-limit response, headers visible in dev tools.

## Enterprise best practices
- Redis-backed store is required for anything running more than one server instance — an in-memory store resets per-process and doesn't coordinate across instances, silently weakening the limit.
- Different limits per endpoint sensitivity — never use one blanket limit for both `/auth/login` and `/health`.
- Log every 429 with the identifying key (user id or IP) — this is your abuse-monitoring signal, cheap to add now.

## Checklist before moving on
- [ ] Auth endpoints have a stricter limit than general API traffic
- [ ] Redis store configured with an in-memory fallback if Redis is down (don't let rate limiting become a single point of failure)
- [ ] `Retry-After` header surfaced meaningfully in the frontend, not swallowed
- [ ] Progressive lockout tested: 429 after N failures, clears on successful login
- [ ] Demo script rehearsed showing an actual 429 on screen

## Common mistakes to avoid
- Rate limiting by IP alone in production — many real users share IPs (corporate NAT, mobile carriers); prefer per-user keys once authenticated.
- Setting limits so tight they trip during your own demo — test your walkthrough script against the limiter before presenting.
- Forgetting `enableOfflineQueue: false` on the Redis client — without it, requests hang waiting on a dead Redis connection instead of falling back gracefully.

## Integration with the rest of the stack
- Uses the same `{ error, code }` response envelope defined in the **Input Validation Generator** skill.
- `req.user.id` used as the rate-limit key comes from the **Role-Based Auth Scaffolder**'s `authenticate` middleware.
