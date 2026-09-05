# PeoplePay360 — Evaluation Summary

**Commit:** `main` @ `167dfb9` · **Verified:** 2026-09-06, against a rebuild
from zero (all four images deleted, `--no-cache`, database volume destroyed).

Everything below was reproduced live by an independent verification pass. Where
something is only proven by a unit test, or could not be demonstrated at all,
it says so in *What is not done* rather than being folded into the claims.

---

## What this is

HR and payroll built around the one thing most HR tooling gets wrong: **an
employee has many contracts over time, and payroll must resolve exactly one of
them for the period being run.** Contract history, working schedules,
attendance, leave balances and deterministic salary rules all follow from
taking that seriously.

---

## What is built and verified working

### Core HR
- **Employees** — create, list, Kanban/List views, detail form, and smart-button
  counters backed by a real counts endpoint.
- **Contracts** — creation, and **overlap rejection enforced by Postgres**, not
  by application code. Two overlapping *active* contracts are refused with a
  business-language 409 naming the conflicting contract and dates. Draft and
  cancelled contracts may overlap freely — they are proposals and history, and
  the exclusion constraint is predicated `WHERE status = 'active'` to say so.
- **Attendance** — check-in/check-out, an open shift correctly reported as
  `missing_checkout`, and HR correction that **requires a reason** (a
  correction without one is refused). Stale-version edits are refused with 409.
- **Time off** — allocation → request → approval, with the balance moving only
  on approval. A **refused** request leaves the balance untouched. Employees
  can act on their own record and are refused (403) on anyone else's.
- **Salary rules** — fixed, percentage-of-another-rule, and **formula** rules.
  The formula grammar is restricted, not `eval()`: `__import__('os').system('id')`
  is refused with *"uses unsupported syntax (Call); only +, -, *, /, %, ** over
  named rule codes and numbers is allowed."*

### Payroll
- **Full wizard** — create → compute → firewall → validate → mark paid,
  verified end to end on a batch containing a deliberately broken employee.
- **The validation firewall is a gate, not a banner.** An employee with no bank
  account produces a **blocking** `missing_bank_details` finding, grouped by
  cause with a title, a fix instruction, and the affected employee named.
  `can_validate` is `false` and validation returns **409** while it stands.
  Fixing the record and recomputing clears it, and validation then succeeds.
- **Loss of Pay, exactly as documented.** Lakshmi Prasad, July net
  **41,800.00** → August net **37,514.29**, with `PP360_LOP` **4,285.71** and
  gross **unchanged at 42,000.00**. The July→August delta equals the LOP line
  to the cent.
- **A paid run is immutable.** Recompute → `409 … a finalized run is never
  recomputed in place`. Delete → `409 … preserved as history`. The run and its
  payslips survive both. The UI greys the buttons *and* the API refuses.
- **Payslip PDF** — a real PDF (verified by magic bytes), whose net, gross and
  every line code match the API's own breakdown.
- **Bulk delivery** — queued per payslip, with per-employee status, attempt
  count, error and timestamp.

### Platform / differentiation layer
- **Payroll dashboard** — real query-backed figures: 274,800.00 net across 5
  payslips for July 2026, average 54,960.00, per-department breakdown, monthly
  trend, attendance and time-off overviews.
- **RBAC is server-side.** Verified by direct API call across **all five roles**
  (12/12 expectations met): Payroll roles and Admin get 200 on the dashboard and
  payruns; **HR Manager and Employee get 403 on both**. HR Manager still gets
  200 on employees. The UI hides the nav *and* the API refuses.
- **Offline sync** — a queued mutation applies on reconnect, and replaying the
  identical `client_mutation_id` returns the **same entity id** and creates **no
  duplicate row**. Cursor pagination advances without repeating rows.
- **AI assistant** — real prose from **groq / openai/gpt-oss-120b**, grounded:
  it quoted the payslip's real 30,000.00 / 42,000.00 / 200.00 / 41,800.00 and
  23.00 worked days, with 8 fact sections and 8 named fact sources beside it.
- **MCP server** (advanced profile, port 8100) — 19 tools over a real
  Streamable-HTTP JSON-RPC handshake. **A valid agent key never widens the
  actor**: an agent acting for an HR Manager still gets
  `403: Role 'hr_manager' is not authorized` on a payroll tool. A wrong key is
  rejected. There is **no `compute` tool** — the AI has no write path to the
  rule engine.
- **Realtime** — approving a request over REST pushed a `time_off.decided`
  frame to an open WebSocket with no polling, and what the frame announced was
  already committed and readable. Invalid tokens and unknown channels are
  refused.
- **Read-side views** — View Calculation (frozen inputs beside persisted
  totals), Payslip Comparison, Contract Time Machine, the Firewall open-gates
  view, and 6 anomaly signals each carrying `current_value` / `baseline` /
  `reason`.

---

## Architecture worth calling out

**Money is `Decimal` end to end, and never becomes a float.** Amounts cross the
API as strings and are rendered as received — `41800.00` stays `41800.00`. The
frontend types say so explicitly and nothing calls `Number()` on an amount.

**Immutability is enforced where it cannot be bypassed.** The contract-overlap
guarantee is a Postgres `EXCLUDE USING gist` constraint with a predicate, not a
service-layer check — the database will not hold the bad state even if code is
wrong. Audit logs are `REVOKE UPDATE, DELETE` for the application role, so the
app *cannot* rewrite its own history. A paid payrun is refused at the service.

**The validation firewall gates a state transition.** Findings are grouped by
code, carry a fix and severity, and `can_validate` is computed from them — a
blocking finding makes validation return 409 rather than warn and proceed.

**RBAC is enforced server-side on every path.** The same `require_role` runs for
a browser request, an MCP tool call, and a queued job — verified by getting 403
through the REST API and through MCP for the same user.

**Payroll mutations require an `Idempotency-Key`**, so a double-clicked or
retried request cannot run payroll twice.

**AI proposes; a human confirms.** An AI-initiated mutation writes nothing to
the domain. It parks a proposal, and a person confirms it, at which point the
*existing* service method runs with that person's identity. The audit trail
carries `AI_PROPOSED_ACTION` (by `ai-agent`) → `AI_CONFIRMED_ACTION` →
`AI_ACTION_EXECUTED`. A confirmed proposal cannot be replayed.

---

## Tests

**434 backend tests pass** against a real Postgres — never SQLite, because the
exclusion constraint and the advisory lock are properties of the database's
constraint system and lock manager and cannot be exercised or mocked otherwise.

The golden payslip test pins **BASIC 30,000.00 → NET 41,800.00** with exact
`Decimal` equality, so a rounding or float regression anywhere in the rule
engine fails the build. The suite also includes an adversarial failure-mode
file covering zero-working-day periods, missing schedules and boundary dates.

The frontend has no test runner; it is gated by `tsc -b && vite build`.

---

## What is not done yet, and why

**Honest disclosure list. None of this is softened.**

1. **Groq is the only AI provider, with an 8000 TPM ceiling and no fallback.**
   Cerebras was removed on 2026-09-06 because its account authenticated but
   returned `402 Payment Required` on every completion — it was never a working
   fallback, only a second failure mode. Consequence: **ask the assistant one
   question at a time.** Two in quick succession legitimately returns
   *"Temporarily unavailable — try again shortly."* This never affects payroll,
   the dashboard or any other screen — verified live: while the AI layer was
   rate-limited, `/health`, `/employees/`, `/payruns/`, `/dashboard/summary`
   and `/payslips/{id}` all returned 200.

2. **The AI single-flight guard is client-side only.** The browser disables the
   Ask button while a request is in flight (verified in a real browser:
   `disabled=true`). The **backend accepts concurrent AI requests** — two
   simultaneous API calls both ran. Nothing enforces one-at-a-time server-side.

3. **The cross-panel case of that guard cannot be demonstrated.** The Ask panel
   requires a payroll role; the Proposal panel requires a linked employee
   record. **No seeded login has both**, so the two panels are never
   simultaneously actionable. The shared-flag code exists and is unit-reachable,
   but there is no clickable path to it in the demo.

4. **`GET /api/v1/sync/pull` returns 500 on a malformed `since` cursor.**
   `since` is an opaque base64 cursor, not a timestamp; passing anything else
   raises `binascii.Error` unhandled instead of returning 400. The correct
   round-trip (feeding back the previous response's `cursor`) works and
   paginates without repeating rows. Real bug, low severity, input-validation
   only.

5. **The bulk-email failure path is not demonstrable in this environment.**
   Per-payslip isolation is real in code — each delivery has its own
   `try/except` that sets that row to `failed` with an error — but the stack
   sends through MailHog, which accepts every address, so an induced "bad"
   address still reports `sent`. Both deliveries succeeding is all that could
   be shown.

6. **The dashboard defaults to a period containing no paid payroll,** so it
   opens showing 0.00 everywhere. The seeded paid run is July 2026; the filter
   must be set to it. Correct behaviour, but it reads as broken to a first-time
   viewer.

7. **No browser/e2e test tooling ships with the repo.** The browser
   walkthrough behind this document used Playwright installed ad hoc outside
   the project. Nothing in CI clicks the UI; the frontend gate is the
   TypeScript build only.

8. **`scripts/rbac_audit.py` writes rows.** Its "own record" probes create a
   real attendance record and time-off request, and it does not clean up. It is
   a useful check but it moves the demo database off its seeded state.

9. **Ollama is referenced as a future local fallback tier** in
   `provider_router.py` and remains a commented-out TODO. Not built.

---

## How to run it

```bash
docker compose up -d                        # core: Phases 1-7, no AI key needed
docker compose --profile advanced up -d     # adds MCP server + OTel collector
```

Frontend <http://localhost:3000> · API docs <http://localhost:8000/docs> ·
MailHog <http://localhost:8025> · MCP <http://localhost:8100/mcp>

Both were verified to come up healthy from a completely clean slate — no
images, no database volume — using only the commands documented in the README.

See [demo-script.md](demo-script.md) for a step-by-step live walkthrough.
