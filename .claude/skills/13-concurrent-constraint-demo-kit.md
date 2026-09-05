# Concurrent Constraint Demo Kit

## Why this matters
Claiming "we prevent double-booking" is cheap. Proving it live, with two genuinely simultaneous requests hitting the database at the same instant, is the single most convincing artifact you can put in front of a judge. This skill is the scripted, rehearsed version of that proof — not improvised on stage.

## Architecture
```
Two requests fired with zero intentional delay between them (Promise.all, not sequential awaits)
        │              │
        ▼              ▼
   Both hit the same Postgres EXCLUDE USING gist constraint
   (from the State Machine + Constraint Generator skill)
        │
        ▼
Exactly one succeeds (201), the other fails cleanly (409) — never both succeeding, never a crash
```
This kit assumes the exclusion constraint from the State Machine skill is already in place — it does not implement conflict prevention itself, it proves it.

## Step 1 — Node.js concurrency script (preferred: precise, reusable, demo-friendly output)
```js
// scripts/demo-concurrent-booking.js
// Run with: node scripts/demo-concurrent-booking.js
const axios = require('axios');

const API_URL = process.env.API_URL || 'http://localhost:4000';
const RESOURCE_ID = process.env.DEMO_RESOURCE_ID; // seed this resource beforehand
const TOKEN = process.env.DEMO_TOKEN; // a valid access token, seed a demo user beforehand

async function attemptBooking(label) {
  const start = performance.now();
  try {
    const { data } = await axios.post(`${API_URL}/bookings`, {
      resourceId: RESOURCE_ID,
      start: '2026-08-10T18:00:00Z',
      end: '2026-08-10T19:00:00Z',
    }, { headers: { Authorization: `Bearer ${TOKEN}` } });
    return { label, ok: true, status: 201, bookingId: data.id, ms: Math.round(performance.now() - start) };
  } catch (err) {
    return {
      label, ok: false,
      status: err.response?.status,
      error: err.response?.data?.error,
      ms: Math.round(performance.now() - start),
    };
  }
}

async function run() {
  console.log('Firing two TRULY simultaneous booking requests for the same resource + time slot...\n');

  const [resultA, resultB] = await Promise.all([
    attemptBooking('Request A'),
    attemptBooking('Request B'),
  ]);

  [resultA, resultB].forEach(r => {
    const outcome = r.ok ? `✅ SUCCEEDED (201, booking ${r.bookingId.slice(0, 8)})` : `❌ REJECTED (${r.status} — "${r.error}")`;
    console.log(`${r.label}: ${outcome}  [${r.ms}ms]`);
  });

  const successCount = [resultA, resultB].filter(r => r.ok).length;
  console.log(`\n${successCount === 1 ? '✅ PASS' : '❌ FAIL'}: exactly one request should succeed. Got ${successCount}.`);
}

run();
```
`Promise.all` is what makes this genuinely simultaneous, not sequential — both requests leave the Node process in the same tick, before either has a response, so there is no artificial ordering advantage.

## Step 2 — Bash/curl alternative (if Node isn't handy on the demo machine)
```bash
#!/usr/bin/env bash
# scripts/demo-concurrent-booking.sh
API_URL="${API_URL:-http://localhost:4000}"
RESOURCE_ID="${DEMO_RESOURCE_ID}"
TOKEN="${DEMO_TOKEN}"

BODY='{"resourceId":"'"$RESOURCE_ID"'","start":"2026-08-10T18:00:00Z","end":"2026-08-10T19:00:00Z"}'

echo "Firing two simultaneous curl requests..."

curl -s -o /tmp/resA.json -w "Request A: %{http_code}\n" -X POST "$API_URL/bookings" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$BODY" &

curl -s -o /tmp/resB.json -w "Request B: %{http_code}\n" -X POST "$API_URL/bookings" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d "$BODY" &

wait

echo "--- Response A ---"; cat /tmp/resA.json; echo
echo "--- Response B ---"; cat /tmp/resB.json; echo
```
The trailing `&` on each `curl` backgrounds it; `wait` blocks until both return — same "fired together" property as `Promise.all`, portable to any machine with bash and curl.

## Step 3 — Direct database-level proof (for a technical judge who wants to see below the API)
```sql
-- Run in two separate psql sessions, submitted within the same second
-- Session 1:
BEGIN;
INSERT INTO "Booking" ("id", "resourceId", "userId", "status", "slot", "createdAt", "updatedAt")
VALUES (gen_random_uuid(), 'demo-resource-id', 'demo-user-id', 'PENDING',
        tstzrange('2026-08-10 18:00+00', '2026-08-10 19:00+00'), now(), now());
COMMIT;

-- Session 2 (run immediately, before or after Session 1 commits):
BEGIN;
INSERT INTO "Booking" ("id", "resourceId", "userId", "status", "slot", "createdAt", "updatedAt")
VALUES (gen_random_uuid(), 'demo-resource-id', 'demo-user-id', 'PENDING',
        tstzrange('2026-08-10 18:00+00', '2026-08-10 19:00+00'), now(), now());
COMMIT;
-- Expected: ERROR: conflicting key value violates exclusion constraint "no_overlapping_bookings"
```
Showing the raw Postgres error in a second terminal window is the most direct possible proof — no API layer in between to accuse of "just checking in code."

## Step 4 — SELECT ... FOR UPDATE fallback demo (for the MySQL/no-exclusion-constraint path)
If your team is on MySQL instead of Postgres, demonstrate the row-locking alternative instead:
```js
// src/services/booking.service.mysql.js
async function createBookingMySQL({ resourceId, start, end, userId }) {
  return db.transaction(async (trx) => {
    const conflict = await trx('bookings')
      .where('resourceId', resourceId)
      .andWhere('status', '!=', 'CANCELLED')
      .andWhere('start', '<', end)
      .andWhere('end', '>', start)
      .forUpdate() // locks matching rows until this transaction commits/rolls back
      .first();

    if (conflict) {
      const err = new Error('This time slot is already booked.');
      err.statusCode = 409;
      throw err;
    }
    return trx('bookings').insert({ resourceId, userId, status: 'PENDING', start, end });
  });
}
```
Be upfront in the demo: "MySQL doesn't have exclusion constraints, so we use `SELECT ... FOR UPDATE` to lock the conflicting row range for the duration of the transaction — the second concurrent request blocks until the first commits, then sees the conflict and is rejected." This shows you understand the tradeoff rather than being unaware of it.

## Clean 409 handling recap (from the State Machine skill, reused here)
```js
} catch (err) {
  if (err.code === '23P01') { // Postgres exclusion_violation
    const clean = new Error('This time slot is already booked for the selected resource.');
    clean.statusCode = 409;
    clean.code = 'SLOT_CONFLICT';
    throw clean;
  }
  throw err;
}
```

## Demo script for judges
> "We're going to prove our conflict prevention isn't just an if-check — it's enforced by the database itself, even under true concurrency."

1. Run `node scripts/demo-concurrent-booking.js` on screen, terminal font large enough to read from the back of the room.
2. Narrate while it runs: "Both requests are fired in the same event loop tick — there's no artificial delay giving one an advantage."
3. Point at the output: "Request A succeeded, Request B was rejected with a clean 409 — not a crash, not a 500, a deliberate conflict response."
4. Optional escalation if a judge pushes further: "Want to see it at the database level directly?" → run the two-`psql`-session version from Step 3.

## Checklist before moving on
- [ ] Script tested and reliably shows exactly one success, one clean rejection — not flaky
- [ ] Demo resource and demo user pre-seeded, IDs saved in an env file, not looked up live
- [ ] Script run at least once against the actual demo-day database, not just localhost dev data
- [ ] Terminal font size increased for room visibility before presenting
- [ ] Fallback (Step 3, raw SQL) rehearsed in case a technical judge asks to go deeper

## Common mistakes to avoid
- Using sequential `await` calls instead of `Promise.all`/backgrounded curl — this artificially serializes the requests and doesn't actually test concurrency.
- Running the demo against a resource/slot that's already booked from an earlier rehearsal — reset demo data right before presenting, or use a demo-only resource seeded fresh.
- Not handling the DB error code translation — a raw Postgres error dumped to the terminal reads as a crash, not a deliberate safeguard, even though the underlying behavior is correct.

## Integration with the rest of the stack
This kit is purely a proof harness for the **State Machine + Constraint Generator** skill's exclusion constraint — build that skill first, this second. It's also the concrete artifact referenced in the **Demo Script Builder**'s required "break it live" moment.
