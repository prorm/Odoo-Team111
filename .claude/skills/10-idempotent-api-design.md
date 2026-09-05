# Idempotent API Design

## Why this matters
Double-clicks and network retries are inevitable in any real demo — a judge clicking "Book" twice, or wifi hiccuping mid-request, should never create two bookings or double-charge a payment. Idempotency is a textbook enterprise API pattern (Stripe, every serious payments API) and directly ties back to the conflict-avoidance judging criterion, just from the client-retry angle instead of the concurrency angle.

## Architecture
```
Client generates a UUID once per user action (not per HTTP attempt)
        │
        ▼
POST /bookings
Header: Idempotency-Key: <uuid>
        │
        ▼
Express middleware
  ├── key seen before + succeeded → return CACHED response, skip business logic entirely
  ├── key seen before + in-flight → return 409 (concurrent duplicate, reject)
  └── key not seen → proceed, then cache the response keyed by (user, key)
```
The cache must store the **exact response** (status + body), not just "was it processed" — a retried request needs the same answer as the original, including the created resource's ID.

## Step 1 — Database schema for idempotency records
```prisma
model IdempotencyKey {
  id           String   @id @default(uuid())
  key          String   // client-supplied Idempotency-Key header
  userId       String
  requestHash  String   // fingerprint of the request body, detects key reuse with different payload
  status       String   // 'IN_PROGRESS' | 'COMPLETED'
  responseCode Int?
  responseBody Json?
  createdAt    DateTime @default(now())
  expiresAt    DateTime

  @@unique([userId, key])
  @@index([expiresAt]) // for cleanup job
}
```
```sql
-- migration adds a cleanup-friendly index and a default 24h expiry pattern
```

## Step 2 — Request fingerprinting
```js
// src/lib/fingerprint.js
const crypto = require('crypto');

function fingerprintRequest(body) {
  const normalized = JSON.stringify(body, Object.keys(body).sort()); // stable ordering
  return crypto.createHash('sha256').update(normalized).digest('hex');
}

module.exports = { fingerprintRequest };
```

## Step 3 — Idempotency middleware
```js
// src/middleware/idempotency.js
const prisma = require('../lib/prisma');
const { fingerprintRequest } = require('../lib/fingerprint');

const TTL_HOURS = 24;

function idempotent() {
  return async (req, res, next) => {
    const key = req.headers['idempotency-key'];
    if (!key) {
      return res.status(400).json({ error: 'Idempotency-Key header is required for this operation', code: 'MISSING_IDEMPOTENCY_KEY' });
    }

    const requestHash = fingerprintRequest(req.body);
    const existing = await prisma.idempotencyKey.findUnique({
      where: { userId_key: { userId: req.user.id, key } },
    });

    if (existing) {
      if (existing.requestHash !== requestHash) {
        return res.status(422).json({
          error: 'Idempotency-Key was reused with a different request payload',
          code: 'IDEMPOTENCY_KEY_CONFLICT',
        });
      }
      if (existing.status === 'IN_PROGRESS') {
        return res.status(409).json({ error: 'A request with this key is already being processed', code: 'DUPLICATE_IN_FLIGHT' });
      }
      // status === COMPLETED — replay the exact original response, no business logic re-runs
      return res.status(existing.responseCode).json(existing.responseBody);
    }

    try {
      await prisma.idempotencyKey.create({
        data: {
          key, userId: req.user.id, requestHash, status: 'IN_PROGRESS',
          expiresAt: new Date(Date.now() + TTL_HOURS * 60 * 60 * 1000),
        },
      });
    } catch (err) {
      if (err.code === 'P2002') { // unique constraint race: two near-simultaneous retries
        return res.status(409).json({ error: 'A request with this key is already being processed', code: 'DUPLICATE_IN_FLIGHT' });
      }
      throw err;
    }

    // capture the response to persist it after the handler runs
    const originalJson = res.json.bind(res);
    res.json = (body) => {
      prisma.idempotencyKey.update({
        where: { userId_key: { userId: req.user.id, key } },
        data: { status: 'COMPLETED', responseCode: res.statusCode, responseBody: body },
      }).catch(err => console.error('[idempotency] failed to persist response:', err));
      return originalJson(body);
    };

    next();
  };
}

module.exports = idempotent;
```

## Step 4 — Wiring into a mutation endpoint
```js
// src/routes/booking.routes.js
const idempotent = require('../middleware/idempotency');

router.post('/bookings', authenticate, validate(createBookingSchema), idempotent(), bookingController.create);
```
Combines cleanly with the Input Validation middleware — validate first (cheap, no DB write), then idempotency check (one lookup), then the actual business logic.

## Step 5 — Cleanup job for expired keys
```js
// src/jobs/cleanupIdempotencyKeys.js
const prisma = require('../lib/prisma');

async function cleanupExpiredKeys() {
  const { count } = await prisma.idempotencyKey.deleteMany({
    where: { expiresAt: { lt: new Date() } },
  });
  if (count) console.log(`[cleanup] removed ${count} expired idempotency keys`);
}

// run on an interval — for hackathon scope, setInterval is fine over a real cron/queue
setInterval(cleanupExpiredKeys, 60 * 60 * 1000); // hourly

module.exports = { cleanupExpiredKeys };
```

## Step 6 — Frontend: generating and reusing the key correctly
```jsx
// src/hooks/useIdempotentSubmit.js
import { useRef } from 'react';

export function useIdempotentSubmit() {
  const keyRef = useRef(crypto.randomUUID()); // generated ONCE per form mount, not per click

  const submit = async (data) => {
    return api.post('/bookings', data, {
      headers: { 'Idempotency-Key': keyRef.current },
    });
  };

  const reset = () => { keyRef.current = crypto.randomUUID(); }; // call after a genuinely new action

  return { submit, reset };
}
```
```jsx
function BookingForm() {
  const { submit, reset } = useIdempotentSubmit();
  const [pending, setPending] = useState(false);

  const handleSubmit = async (data) => {
    if (pending) return; // belt-and-suspenders: disable double-click at the UI layer too
    setPending(true);
    try {
      await submit(data);
      reset(); // new key for the NEXT booking, not this one
    } finally {
      setPending(false);
    }
  };
  // ...
}
```
The key must survive retries of the *same* user action (network failure → axios retry → same key) but must be regenerated for a genuinely new action (user submits, succeeds, then books again) — that distinction is the entire point of the pattern.

## Payment/order-style example (the canonical idempotency use case)
```js
// even though this hackathon likely won't process real payments, the same pattern applies
router.post('/orders/:id/charge', authenticate, idempotent(), async (req, res, next) => {
  try {
    const charge = await paymentService.charge(req.params.id, req.body.amount);
    res.status(201).json(charge);
  } catch (err) { next(err); }
});
```
Without idempotency here, a network timeout followed by an automatic client retry could charge a customer twice — this is precisely why Stripe's API requires an `Idempotency-Key` on every charge request.

## Checklist before moving on
- [ ] Every non-idempotent-by-nature mutation (`POST` that creates a resource) requires the header
- [ ] Same key + different payload → `422`, not silently processed
- [ ] Same key + in-flight duplicate → `409`, not a race that creates two records
- [ ] Same key + completed → exact original response replayed, no business logic re-run
- [ ] Frontend generates the key once per logical action, not once per HTTP attempt

## Common mistakes to avoid
- Generating a new `Idempotency-Key` on every `submit()` call instead of once per form/action — defeats the entire purpose, since a retry needs to reuse the *same* key.
- Storing only "processed: true/false" instead of the full response — a retry then can't return the created resource's actual ID.
- Not handling the unique-constraint race in Step 3's catch block — two genuinely simultaneous retries with the same key need a clean `409`, not an unhandled DB error.

## Integration with the rest of the stack
- Composes directly after **Input Validation Generator**'s `validate()` middleware in the route chain.
- Ties into **State Machine + Constraint Generator**: idempotency prevents duplicate `createBooking` calls; the exclusion constraint prevents overlapping *different* bookings — two complementary guarantees, not redundant ones.
