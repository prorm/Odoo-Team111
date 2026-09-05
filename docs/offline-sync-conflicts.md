# Offline sync — conflicts, and why the conflict path is unreachable

Phase 8 registers two syncable entities (Architecture §8.3), both **CREATE-only**:

| Entity | Syncable | Never syncable |
|---|---|---|
| `attendance` | check-in / check-out **creation** | corrections (`PATCH`), deletion |
| `time_off_request` | **submission** | approval, refusal, edit, deletion |

This document answers the question that registration raises: the engine has a
whole `Keep Mine` / `Overwrite` conflict-resolution mechanism — when does it
fire for these two?

**It cannot.** Not "rarely", not "we have not seen it" — it is unreachable by
construction, and this file exists so that is a recorded decision rather than an
untested branch somebody later assumes was working.

---

## Why it is unreachable

A `conflict` outcome is produced in exactly one place,
`SyncService._apply_one`:

```python
current = await service.repo.get_by_public_id(mutation.entity_id)
...
if mutation.known_version != current.version:
    ...
    return conflict_result
```

Two facts put both registered entities on the other side of that code:

1. **`_apply_one` returns before reaching it for a CREATE.** A create has no
   `entity_id` and no `known_version` — there is no existing row whose version
   could disagree with anything. The version check is only on the UPDATE and
   DELETE paths.
2. **Neither entity accepts UPDATE or DELETE.** Both register
   `allowed_ops=frozenset({"CREATE"})`, and `SyncService.push` rejects a
   disallowed op with `OPERATION_NOT_SYNCABLE` *before* `_apply_one_guarded`
   is called at all.

So for `attendance` and `time_off_request`, `outcome == "conflict"` is not
reachable, `addConflict` is never called for them, and `ConflictModal` never
opens on their account.

`tests/test_offline_sync.py::test_corrections_are_not_syncable` and
`::test_time_off_approval_is_not_reachable_through_sync` pin the second fact
from the outside: they push an UPDATE and a DELETE, assert the rejection code,
and then assert the stored row is **byte-identical** — because "the response
said rejected" and "nothing was written" are different claims.

## Why the mechanism is still there

It is not dead code kept out of sentiment. `version_id_col`, the conflict
envelope carrying `current_state`, the outbox's `conflict` status and the
resolution modal are what an UPDATE-capable registration would need on the day
someone adds one, and the engine is deliberately entity-agnostic. Deleting them
would mean rebuilding them, less carefully, under time pressure.

What Phase 8 changed is that adding such a registration is now a decision with
a shape: an entity that wants UPDATE has to widen `allowed_ops`, and at that
moment the conflict path becomes live and this document becomes wrong.

## Why "corrections stay online-only" is the right cut, not a shortcut

It would have been easy to register `attendance` for UPDATE too and let the
conflict machinery sort it out. That would have been worse in three ways:

- **It bypasses a role gate.** A correction is `AttendanceService.correct`,
  which starts with `require_hr(user)` and demands a correction reason recorded
  in the audit trail. The engine's generic UPDATE path has neither. Registering
  UPDATE would let an offline device rewrite an attendance record with no HR
  role and no reason — the correction gate bypassed by choosing a different verb.
- **It has no correct answer.** `Keep Mine` on an attendance correction means
  "the device that was offline longest wins", which is exactly backwards: the
  correction made by HR with the roster in front of them is the one that should
  survive.
- **It is not what the offline case needs.** A device with no network needs to
  record that somebody *arrived*. It does not need to relitigate a record that
  already exists on the server.

The same argument, more sharply, for time-off approval: approval debits a live
allocation inside a transaction (`TimeOffRequestService._approve`). A device
holding a stale copy of a balance must not be allowed to decide that the
balance is sufficient. Submission is safe precisely because **a pending request
reserves nothing** — the balance check happens at approval, online, against the
row as it is then.

---

## The edge cases that DO exist

Version conflicts are unreachable; these are not, and each is handled by an
existing mechanism rather than a new one.

### 1. The same mutation arriving twice — the one PRD §7 measures

A client sends successfully, the network drops before the response arrives, and
the queued mutation is retried on reconnect. Handled by the idempotency key:
`push` looks up `(actor_key, client_mutation_id)` in `sync_mutations` and
**replays the stored result verbatim**, so the retry returns the same
`entity_id` the first attempt did rather than creating a second row or claiming
the row does not exist.

`client_mutation_id` is generated when the mutation is **queued**, not when it
is sent (`useOfflineMutation.create`), which is what makes every retry of one
queued mutation carry one id.

Covered by `test_retried_mutation_id_creates_exactly_one_row`, which counts
rows in PostgreSQL, and by `test_idempotency_is_scoped_to_the_actor`, which
shows the key includes the actor so two people's devices cannot collide.

### 2. A mutation that is simply not allowed

Queued offline, refused on arrival: attendance for somebody else, a payload
carrying server-owned fields, a leave type that was deleted while the device was
away, an employee record that was removed. These come back as `rejected` with a
code (`FORBIDDEN`, `VALIDATION_ERROR`, `NOT_FOUND`) and are **terminal** — the
client drops them from the outbox rather than retrying forever.

This is a real gap in the UI, and it is stated rather than hidden: the frontend
currently logs a rejected mutation to the console
(`sync-engine.ts`, `sendBatch`) instead of showing the person what was refused
and why. The mutation is not silently lost — it is visibly gone from "waiting
to sync", and the server has an audit trail — but a queued check-in that the
server refuses deserves a message. See ROADMAP.md's known-gaps table.

Crucially, one rejection does not strand the rest of the outbox:
`test_one_rejected_mutation_does_not_abort_the_batch` pushes a refused mutation
and a valid one in one batch and asserts the valid one applied.

### 3. Two check-ins for the same day

Two offline check-ins produce two rows, each with its own
`client_mutation_id` — so idempotency, correctly, does not merge them. There is
no unique constraint on `(employee, date)` and there should not be: multiple
shifts in one day are legitimate, and `build_payroll_context` already counts
**distinct dates** rather than rows.

This is not an offline-specific behaviour — the same two taps online produce the
same two rows — and the remedy is the same: HR deletes the spurious row, which
is an online, role-gated, audited action.

### 4. Device clock skew

An offline check-in is timestamped by the **device**, because the check-in
happened when the person arrived and not when their phone found a signal. A
device with a wrong clock therefore writes a wrong `check_in`.

The server still owns everything derived from it: `worked_hours` and `status`
are computed by `AttendanceService._compute` from the timestamps, never taken
from the payload (`test_server_derives_worked_hours_and_status_not_the_client`),
and `AttendanceCreate` is `extra="forbid"` so a payload cannot smuggle them in
(`test_client_cannot_smuggle_server_owned_fields`).

A wrong timestamp is a wrong *input*, which is what HR corrections exist for —
online, role-gated and audited. This is consistent with PRD §8's stated
assumption that attendance is manually entered and corrected, with no hardware
attestation. It is a deliberate cost of offline attendance, not an oversight.

### 5. A leave request queued against a balance that is gone

An employee queues a request offline; by the time it syncs, their allocation has
been consumed. The request is still **created** — and that is correct, because a
pending request reserves nothing either way. It will fail at *approval*, with the
same insufficient-balance message an online request would get, at the moment a
human tries to approve it.

The offline path changes nothing here, which is the point: submission was always
the safe half of the workflow.

### 6. An auto-approving leave type

A type with `requires_approval=False` auto-approves on submission and debits an
allocation in the same transaction. Synced offline, that debit happens **at
reconnect**, not at submission — against the balance as it stands then. If it is
insufficient, the whole mutation is rejected and no row is left behind
(`create_request` runs inside a savepoint).

This is the one place where an offline submission does touch a balance, and it
is worth knowing about. It is still not a conflict: it is a create that either
applies completely or does not apply at all.

---

## Two limitations the offline demo actually hit

Both were found by running the kill-network cycle in a browser, not by reading
the code, and both are stated here rather than quietly worked around.

### A hard page load while offline fails

There is **no service worker**, so the app shell is not cached. In-app
navigation works offline (React Router never touches the network), and every
screen already loaded keeps working — but pressing reload, or opening the app
cold with no signal, gets the browser's offline error page.

That is the difference between an offline-capable data layer, which this is,
and an installable PWA, which this is not. Adding a service worker is real work
with its own failure modes — a stale shell served to someone who has just been
given a fix is worse than an error page — and it is not sync infrastructure, so
it belongs on the roadmap rather than smuggled into this phase.

The offline demo asserts this behaviour explicitly rather than stepping around
it, so that the day someone adds a service worker the assertion fails and this
paragraph gets deleted deliberately.

### Reference data the forms need is cached separately

The leave-request form cannot be filled in without the list of leave types, and
that list is a live read (`/time-off-types/lookup`). Offline, the dropdown was
empty — so "you may submit a request offline" was false in the only way that
matters.

`time_off_type` is **not** registered as a syncable entity, and must not be:
Architecture §8.3 names exactly two. Instead `useOfflineReferenceList` caches
the lookup into the SAME IndexedDB store the sync engine already uses, under its
own key, written whenever the online read succeeds and read back when it fails.
It is never pushed and never pulled, and `/sync/pull` is never asked for it.
Types are configuration, not somebody's records — there is nothing here to
conflict, reconcile or push.

Two consequences worth knowing. The cache is warmed by opening the Time Off
screen online — which is why the page, not only the form, requests the list; the
form mounts on demand, so a person who never opened it online would have found
an empty dropdown at exactly the moment it needed to work. And a brand-new
device that has never had a connection has nothing cached, and shows the real
error rather than an empty dropdown.

---

## Summary


| Situation | Mechanism | Outcome |
|---|---|---|
| Retried mutation | `sync_mutations` idempotency key | Replayed; exactly one row |
| Two different check-ins | Distinct client mutation ids | Two rows, correctly |
| Not authorised / invalid | Domain service raises; engine maps to `rejected` | Terminal, batch continues |
| Correction or approval attempted | `allowed_ops` | `OPERATION_NOT_SYNCABLE`, row untouched |
| **Version conflict** | — | **Unreachable for both registered entities** |
