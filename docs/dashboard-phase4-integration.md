# Dashboard ↔ Phase 4 Integration Boundary

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
2. `Payslip.status` is a `PayslipStatus` enum (`draft, computed, validated, paid,
   cancelled`). "Actually paid" (§4.A of the data contract) means `status == 'paid'`,
   literally.
3. `Payslip.payrun_id → Payrun.period_start/period_end` is how a payslip's period is known;
   `Payslip` itself carries no period columns.
4. `Payslip.employee_id` and `Payslip.contract_id` are both present; the dashboard groups
   money by `Payslip.employee.department_id` (current department), not
   `Payslip.contract.department_id` (department at contract time) — see the data contract
   §5.F for why.
5. `Payslip.warnings` is a nullable `JSONB` list. Its *shape* (what keys each entry carries)
   is not yet defined anywhere in the codebase — Phase 4 has not written to it yet.

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

## Post-merge integration checklist

1. Confirm `Payslip.net_amount`/`gross_amount` are populated by compute exactly as this
   schema already declares (they should be — no schema change expected).
2. Confirm the `warnings` entry shape against `_normalize_warning`'s recognized categories;
   adjust the recognized-keys table if Phase 4 used different field names.
3. Re-run `tests/test_dashboard.py` against real computed payslips from an actual payrun
   (in addition to its existing directly-seeded fixtures) as a final sanity check.
4. No change is anticipated to `app/api/v1/routers/dashboard.py`, `app/schemas/dashboard.py`,
   or any frontend file — the integration is confined to `app/services/dashboard.py`'s
   payroll-reading functions, and only if item 2 above turns up a mismatch.
