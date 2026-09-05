# Dashboard ↔ Phase 4 Integration Boundary

> **STATUS: INTEGRATED.** Phase 4 has landed and `phase-6-dashboard` was
> rebased onto it. Everything below was written *before* Phase 4 existed; each
> assumption now carries a **CONFIRMED** or **CORRECTED** verdict checked
> against the real code, not against this document's own predictions. The
> integration changed dashboard-side reads only — no Phase 4 computation,
> resolver call, transaction/locking or warning-generation code was modified.
>
> | # | Assumption | Verdict |
> |---|---|---|
> | 1 | `Payslip.net_amount` is the authoritative total | **CONFIRMED** |
> | 2 | `Payslip.status == 'paid'` means actually paid | **CONFIRMED** |
> | 3 | Period comes from `Payrun.period_start/period_end` | **CORRECTED** — join needed `Payrun.deleted_at IS NULL` |
> | 4 | `Payslip.employee_id`/`contract_id`; group by employee's department | **CONFIRMED** |
> | 5 | `Payslip.warnings` entry shape is undefined | **CORRECTED** — shape is now defined, and the normalizer was wrong |
>
> Two further checks the post-merge checklist called for:
>
> | Check | Verdict |
> |---|---|
> | "Payslips Generated" does not double-count a recomputed payrun | **CONFIRMED** |
> | `PayrunStatus` has an "Approved" state (per Phase 6's older prose) | **CORRECTED** — it is DRAFT → COMPUTED → VALIDATED → PAID |

The dashboard (this branch, `phase-6-dashboard`) and the payroll compute engine (Phase 4,
built in parallel on a separate branch) meet at exactly one place: the `Payrun`/`Payslip`/
`PayslipLine` tables in `app/models/payroll.py`. This document records what the dashboard
assumes about that schema today, so integration is "point queries at the real writer" and
never "redesign the dashboard" or "duplicate payroll math."

## What the dashboard already assumes (and why it's safe to assume)

These are properties of the **schema as it exists right now** on this branch, not
predictions about Phase 4's implementation — `app/models/payroll.py` is schema-complete,
only its writer (the compute engine) is pending:

1. `Payslip.net_amount` (`Numeric(12,2)`) is the authoritative total. The dashboard never
   sums `PayslipLine.amount` by category to reconstruct it — it reads the column directly,
   per the model docstring: "Denormalised totals... Written only by the rule engine."

   **CONFIRMED.** `PayrunService._compute_one` writes `net_amount`/`gross_amount` from
   `_totals(resolved)` in the same transaction as the lines they summarise, so the column
   and the lines cannot disagree. No dashboard change.

2. `Payslip.status` is a `PayslipStatus` enum (`draft, computed, validated, paid,
   cancelled`). "Actually paid" (§4.A of the data contract) means `status == 'paid'`,
   literally.

   **CONFIRMED.** `PayrunService.mark_paid` sets `payslip.status = PayslipStatus.PAID` for
   every payslip in the run, and is reachable only from `VALIDATED` (which is itself
   reachable only from `COMPUTED`, and only with zero blocking issues). The enum member
   names are exactly as assumed. Note for anyone reading Phase 6's earlier prose: there is
   **no "Approved" status** — the ratchet is DRAFT → COMPUTED → VALIDATED → PAID. No
   dashboard change.

3. `Payslip.payrun_id → Payrun.period_start/period_end` is how a payslip's period is known;
   `Payslip` itself carries no period columns.

   **CORRECTED** (the join, not the assumption). The period columns are exactly as assumed,
   but the shared predicate `_period_overlaps_payrun` was missing
   `Payrun.deleted_at IS NULL`. Phase 4's `delete_payrun` SOFT-deletes the run and
   deliberately leaves its payslips in place, so a deleted draft/computed payrun's payslips
   were still counted by `payslips_generated` and still appeared in the warning feed. The
   money KPIs escaped it only because they filter `status == PAID` and Phase 4 refuses to
   delete a validated or paid run — an accident, not a design. Predicate fixed; regression
   test added.

4. `Payslip.employee_id` and `Payslip.contract_id` are both present; the dashboard groups
   money by `Payslip.employee.department_id` (current department), not
   `Payslip.contract.department_id` (department at contract time) — see the data contract
   §5.F for why.

   **CONFIRMED.** Both FKs are populated on every payslip (`contract_id` is `NOT NULL` and
   Phase 4 resolves exactly one contract per employee per period before writing). The
   Employee/Contract relationships are unchanged from Phase 1, so department grouping and
   the "Contract Attention" queries hold as written. No dashboard change.

5. `Payslip.warnings` is a nullable `JSONB` list. Its *shape* (what keys each entry carries)
   is not yet defined anywhere in the codebase — Phase 4 has not written to it yet.

   **CORRECTED.** The shape is now defined by `PayrollWarning.as_dict`
   (`app/services/payroll_context.py`) and is
   `{"code", "severity", "message", "references"}`. `_normalize_warning` was rewritten to
   read those four exactly. See "What actually broke" below — the mismatch was worse than a
   key name.

## What actually broke (assumption 5, in detail)

`_normalize_warning` guessed at three possible key names —
`category` → `type` → `code` — and returned only `(category, message)`. Against Phase 4's
real entries that produced two failures, and the second is the one that mattered:

1. **`severity` and `references` were dropped on the floor.** Phase 4's entire
   blocking/advisory distinction — the thing PRD §5.10's pre-finalization gate is built on
   — never reached the dashboard, and neither did the public ids that let a user navigate
   to the offending record.
2. **Four of the six real codes were flattened to `"other"`.** The recognized-set filter
   predated Phase 4 and listed `missing_contract` and `contract_attention`, two codes Phase
   4 never emits. Of what Phase 4 actually writes, only `missing_bank_details` and
   `duplicate_payslip` survived; `missing_checkout`, `contract_gap`, `structure_mismatch`
   and `no_attendance` all rendered identically as "Other". A payrun blocked by a missing
   check-out looked, on the dashboard, exactly like a payrun blocked by nothing in
   particular.

The fix reads the four real keys, passes `code` through **verbatim** (so an alert and the
payslip warning it came from say the same word and can be cross-referenced), and reports an
unknown code with `recognized: false` rather than relabelling it — a code this dashboard has
not been taught about is a *new Phase 4 warning*, and silently renaming it is how a blocking
payroll issue stops being visible. Display text stays in the frontend's `WARNING_LABELS`.

The real codes, for reference:

| Code | Severity | Written by |
|---|---|---|
| `missing_bank_details` | blocking | `warning_checks` |
| `missing_checkout` | blocking | `warning_checks` |
| `contract_gap` | blocking | `warning_checks` |
| `duplicate_payslip` | blocking | `warning_checks` |
| `structure_mismatch` | advisory | `warning_checks` |
| `no_attendance` | advisory | `warning_checks` |
| `no_payslip` | blocking | `validation_report` — derived at Validate, never persisted on a payslip (there is no payslip) |

## "Payslips Generated" and recompute — CONFIRMED, no double-counting

Phase 4's recompute **HARD-deletes** the run's payslips before rewriting them
(`_delete_payslips`: a soft delete would leave a tombstone occupying
`uq_payslip_payrun_employee` and the next compute would collide with it). So a payrun
computed twice holds exactly one payslip per employee, and `payslips_generated` counts it
once. The `uq_payslip_payrun_employee` constraint is the backstop.

Two payslips for one employee in the dashboard's window therefore means two *different*
payruns over overlapping periods — which is real, is what the period-overlap filter is
supposed to show, and is itself flagged by Phase 4 as a blocking `duplicate_payslip`
warning. Counting it once would hide a genuine double-payment risk.

## What Phase 4 needs to preserve

Nothing above requires Phase 4 to change course — it is written directly off the schema
Phase 4 already owns. The one open question is:

**`Payslip.warnings` entry shape.** The dashboard's warning normalizer
(`app/services/dashboard.py::_normalize_warning`) currently recognizes entries shaped like
`{"category": "<one of missing_bank_details | duplicate_payslip | missing_contract |
contract_attention>", "message": "<string>"}` and falls back to `category: "other"` for
anything else (including a bare string). If Phase 4 lands a different shape, the
recommended fix is to either:
  - emit that shape (cheapest — zero dashboard changes), or
  - update only `_normalize_warning`'s recognized-keys table to match Phase 4's actual
    field names.

Nothing else in the dashboard reads `warnings`' internal structure.

## What the dashboard does NOT depend on

- **No dependency on `PayrunStatus`** beyond using it for the period-overlap join
  (`period_start`/`period_end` exist regardless of status). The dashboard does not care
  whether a `Payrun` is `draft`, `computed`, `validated`, or `paid` — it filters on
  `Payslip.status` instead, since a payrun can contain payslips at different points if a
  partial re-run ever happens.
- **No dependency on how compute resolves a contract, executes rules, or writes
  `PayslipLine`.** The dashboard never reads `PayslipLine` rows for KPI purposes (only
  Payslip Explainability, §5.6 of the PRD, would — and that is out of this branch's scope).
- **No dependency on `Idempotency-Key` or `acquire_entity_lock`** — those govern the write
  path; the dashboard is read-only and takes no lock.

## Today's actual behavior (before Phase 4 merges)

Every payroll-dependent query in `app/services/dashboard.py` is real SQL against the real
columns above. Run against the current database (no compute endpoint exists yet, so no
`Payslip` rows exist outside of what a test seeds directly), they correctly return empty/
zero results — `total_net_salary_paid: "0.00"`, `payslips_generated: 0`,
`salary_cost_by_department: []`, etc. This is the correct, honest behavior: an empty
aggregate over an empty table, not a hardcoded placeholder. `tests/test_dashboard.py`
proves the queries are correct by inserting `Payrun`/`Payslip` rows directly through the
ORM (the same way `tests/conftest.py` already seeds `Contract`/`SalaryRule` rows for other
suites) and hand-computing the expected aggregates — this exercises the dashboard's SQL,
not Phase 4's compute logic, and does not simulate or duplicate any payroll calculation.

## Post-merge integration checklist — COMPLETED

1. ~~Confirm `Payslip.net_amount`/`gross_amount` are populated by compute exactly as this
   schema already declares.~~ **DONE — confirmed, no schema change.**
2. ~~Confirm the `warnings` entry shape against `_normalize_warning`'s recognized
   categories.~~ **DONE — mismatched; `_normalize_warning` rewritten.** See "What actually
   broke" above.
3. ~~Re-run `tests/test_dashboard.py` against real computed payslips.~~ **DONE.** The suite
   now includes tests that build Phase 4-shaped `warnings` entries (real codes, real
   severities, real `references`) and assert the dashboard categorizes them correctly,
   including the four codes the old normalizer flattened. These are regression tests for
   exactly this bug.
4. ~~No change anticipated to the router, schemas, or frontend.~~ **PARTLY WRONG.** The
   service fix was necessary but not sufficient: `PayrollWarning` in
   `app/schemas/dashboard.py` and `PayrollWarning` in `frontend/src/types/dashboard.ts`
   both had to carry `code`/`severity`/`references` instead of `category`, or the corrected
   data would have been dropped at the response boundary — the schema would have serialised
   a field the service no longer returned. `ReportsPage.tsx` now colours blocking findings
   differently from advisories and labels all seven real codes.
   `app/api/v1/routers/dashboard.py` was, as predicted, untouched.

## Scope of the integration change

Modified (dashboard-side reads only):
`app/services/dashboard.py`, `app/schemas/dashboard.py`, `tests/test_dashboard.py`,
`frontend/src/types/dashboard.ts`, `frontend/src/routes/reports/ReportsPage.tsx`.

**Not modified:** `app/services/payroll.py`, `app/services/payroll_context.py`,
`app/services/salary_resolver.py`, `app/models/payroll.py`, `app/schemas/payroll.py`,
`app/api/v1/routers/payroll.py`, or any migration. The dashboard was fixed to read what
Phase 4 already writes; Phase 4's computation, resolver calls, transaction/locking and
warning-generation logic are byte-identical to what landed on `dev`.
