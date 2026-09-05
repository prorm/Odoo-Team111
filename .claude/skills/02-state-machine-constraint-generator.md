# State Machine + Constraint Generator

## Why this matters
This is the single most heavily weighted judging criterion: conflict avoidance and relational modeling. Judges deliberately try to break booking/allocation logic live. The bar is "mathematically airtight" — not an `if` check before an insert, but a guarantee enforced at the database layer that survives race conditions, concurrent requests, and buggy application code.

## Architecture
```
Request → Service Layer
            ├── canTransition(from, to)   — in-memory guard, fast rejection, good error message
            └── Prisma transaction
                  ├── UPDATE ... WHERE status = <from>   — optimistic guard
                  └── EXCLUDE constraint at DB level      — physically prevents overlap
                        │
                        ▼
                  audit_log INSERT (who/what/when/why) — see Immutable Audit Log skill
```
Two independent layers: the **transition guard** (business rules: what states can follow what) and the **exclusion constraint** (physical impossibility of two overlapping active rows). Both are required — the guard gives clean errors, the constraint gives correctness under concurrency that no amount of application code can match.

## Step 1 — Define the state machine explicitly
```js
// src/domain/booking.states.js
const TRANSITIONS = {
  PENDING:     ['CONFIRMED', 'CANCELLED'],
  CONFIRMED:   ['IN_PROGRESS', 'CANCELLED'],
  IN_PROGRESS: ['COMPLETED'],
  COMPLETED:   [],
  CANCELLED:   [],
};

function canTransition(from, to) {
  return TRANSITIONS[from]?.includes(to) ?? false;
}

function assertTransition(from, to) {
  if (!canTransition(from, to)) {
    const err = new Error(`Illegal transition: ${from} → ${to}`);
    err.statusCode = 409;
    err.code = 'ILLEGAL_TRANSITION';
    throw err;
  }
}

module.exports = { TRANSITIONS, canTransition, assertTransition };
```
Write the transition map before writing a single line of route code. It doubles as living documentation for the whole team.

## Step 2 — Prisma schema with enum-backed status
```prisma
enum BookingStatus {
  PENDING
  CONFIRMED
  IN_PROGRESS
  COMPLETED
  CANCELLED
}

model Booking {
  id         String        @id @default(uuid())
  resourceId String
  userId     String
  status     BookingStatus @default(PENDING)
  slot       Unsupported("tstzrange")   // Postgres range type, see raw SQL below
  createdAt  DateTime      @default(now())
  updatedAt  DateTime      @updatedAt

  resource   Resource      @relation(fields: [resourceId], references: [id])
  user       User          @relation(fields: [userId], references: [id])

  @@index([resourceId, status])
}
```
Prisma doesn't natively model exclusion constraints or range types — add them via a raw migration.

## Step 3 — The exclusion constraint (the load-bearing part)
```sql
-- prisma/migrations/xxxx_add_booking_exclusion/migration.sql
CREATE EXTENSION IF NOT EXISTS btree_gist;

ALTER TABLE "Booking"
  ADD COLUMN slot tstzrange;

-- backfill existing rows if any, then:
ALTER TABLE "Booking"
  ALTER COLUMN slot SET NOT NULL;

ALTER TABLE "Booking"
  ADD CONSTRAINT no_overlapping_bookings
  EXCLUDE USING gist (
    "resourceId" WITH =,
    slot WITH &&
  ) WHERE (status NOT IN ('CANCELLED'));
```
This makes it **physically impossible** for two non-cancelled bookings on the same resource to have overlapping time ranges — no race condition, no missed `if` check, no bug in application code can violate it. This is the single strongest artifact to show a judge who asks "how do you prevent double-booking?"

## Step 4 — Service layer wiring both guards together
```js
// src/services/booking.service.js
const prisma = require('../lib/prisma');
const { assertTransition } = require('../domain/booking.states');
const { logAudit } = require('./audit.service');

async function createBooking({ resourceId, userId, start, end }) {
  try {
    return await prisma.$transaction(async (tx) => {
      const booking = await tx.$queryRaw`
        INSERT INTO "Booking" ("id", "resourceId", "userId", "status", "slot", "createdAt", "updatedAt")
        VALUES (gen_random_uuid(), ${resourceId}, ${userId}, 'PENDING',
                tstzrange(${start}::timestamptz, ${end}::timestamptz), now(), now())
        RETURNING *;
      `;
      await logAudit(tx, { entity: 'Booking', entityId: booking[0].id, action: 'CREATE', actorId: userId });
      return booking[0];
    });
  } catch (err) {
    if (err.code === '23P01') { // Postgres exclusion_violation
      const clean = new Error('This time slot is already booked for the selected resource.');
      clean.statusCode = 409;
      clean.code = 'SLOT_CONFLICT';
      throw clean;
    }
    throw err;
  }
}

async function transitionBooking(bookingId, toStatus, actorId) {
  return prisma.$transaction(async (tx) => {
    const current = await tx.booking.findUnique({ where: { id: bookingId } });
    if (!current) { const e = new Error('Booking not found'); e.statusCode = 404; throw e; }

    assertTransition(current.status, toStatus);

    const updated = await tx.booking.updateMany({
      where: { id: bookingId, status: current.status }, // optimistic concurrency guard
      data: { status: toStatus },
    });
    if (updated.count === 0) {
      const e = new Error('Booking was modified concurrently, please retry');
      e.statusCode = 409;
      throw e;
    }
    await logAudit(tx, { entity: 'Booking', entityId: bookingId, action: `TRANSITION_${toStatus}`, actorId });
    return tx.booking.findUnique({ where: { id: bookingId } });
  });
}

module.exports = { createBooking, transitionBooking };
```

## Step 5 — Controller translating DB errors to clean HTTP responses
```js
// src/controllers/booking.controller.js
const bookingService = require('../services/booking.service');

async function create(req, res, next) {
  try {
    const booking = await bookingService.createBooking({ ...req.body, userId: req.user.id });
    res.status(201).json(booking);
  } catch (err) { next(err); } // global error handler formats err.code / err.statusCode
}

async function transition(req, res, next) {
  try {
    const booking = await bookingService.transitionBooking(req.params.id, req.body.status, req.user.id);
    res.json(booking);
  } catch (err) { next(err); }
}

module.exports = { create, transition };
```

## Demo/presentation tips
- Stage the "break it" moment explicitly: open two tabs, submit the same slot from both within a second of each other. One succeeds, the other returns a clean `409 SLOT_CONFLICT` — not a crash.
- Also demo an illegal transition attempt (e.g. `COMPLETED → PENDING`) returning `409 ILLEGAL_TRANSITION`.
- Say out loud: "this isn't just an if-check — it's a Postgres exclusion constraint, so it holds even under concurrent load." Judges specifically listen for this distinction.

## Checklist before moving on
- [ ] `TRANSITIONS` map covers every status and has no unreachable states
- [ ] Exclusion constraint applied via migration and verified with a manual concurrent-insert test
- [ ] Every status-changing endpoint calls `assertTransition` before writing
- [ ] DB error code `23P01` is caught and translated to a clean message, never leaked raw
- [ ] Audit log entry written inside the same transaction as the state change (atomicity)

## Common mistakes to avoid
- Checking for overlap with a `SELECT` before `INSERT` and trusting it — this has a race condition window; the exclusion constraint is what actually closes it.
- Forgetting `WHERE (status NOT IN ('CANCELLED'))` on the constraint — without it, cancelling and rebooking the same slot becomes impossible.
- Allowing the frontend to send an arbitrary `status` value directly — always route status changes through `transitionBooking`, never a generic `PATCH /bookings/:id`.

## Integration with the rest of the stack
- `actorId` passed into `logAudit` ties directly into the **Immutable Audit Log Generator** skill.
- The **AI Decision-Node Pattern** skill calls this exact `assertTransition`/`transitionBooking` pair — an AI agent is just another caller that must pass the same guard.
- The **Concurrent Constraint Demo Kit** skill is the scripted version of the "break it live" demo described above.
