# Payroll Dashboard — Data Contract

PS A7 / B9. This document is the source of truth for what `GET /api/v1/dashboard/summary`
returns, where every number comes from, and what each aggregation actually means. It is
written against the **real, current** schema in `app/models/*` — nothing here is aspirational.

Companion doc: [`dashboard-phase4-integration.md`](./dashboard-phase4-integration.md) tracks
exactly which of the sections below depend on Phase 4 (the payroll compute engine) actually
writing rows, as opposed to depending only on the schema that already exists.

## 1. Endpoint

```
GET /api/v1/dashboard/summary
```

**RBAC** (Architecture §5): the whole endpoint requires `PAYROLL_ROLES` —
`hr_payroll_user`, `hr_payroll_manager`, `admin`. `hr_manager` and `employee` get a 403.
This is deliberately stricter than "hide the nav item" — the dashboard aggregates payroll
money (`Payslip.net_amount`), and Architecture §5's matrix gives HR Manager zero payroll
access. Every non-payroll widget (attendance, time off) is folded into the same
authorization boundary rather than split into a separately-gated endpoint, because PS B9
describes one dashboard, and a manager who can't see payroll numbers has no PS-defined
reason to see half of this screen either.

## 2. Filters

Query parameters, applied consistently to every widget that a filter is meaningful for
(§7 documents the two exceptions):

| Param | Type | Default | Notes |
|---|---|---|---|
| `period_start` | `date` | first day of the current month | Inclusive. |
| `period_end` | `date` | last day of the current month | Inclusive. 400 if before `period_start`. |
| `department_id` | `str` (public id) | none | Filters to one `Department`. 404 if it doesn't decode/exist. |
| `employee_type` | `EmployeeType` enum | none | `permanent \| contract \| intern \| part_time` — a real column on `Employee` (Architecture §4), not invented for this feature. |

**Period semantics** — two different shapes, because the underlying data is two different
shapes:

- **Flow widgets** (payroll, attendance, time-off *activity*) use **date-range overlap**:
  a row counts if its own range intersects `[period_start, period_end]`. For a `Payrun`
  this is `Payrun.period_start <= period_end AND Payrun.period_end >= period_start`; for
  `Attendance` it is `check_in::date BETWEEN period_start AND period_end`; for
  `TimeOffRequest` it is the same overlap test against `date_from`/`date_to`.
- **Balance widgets** (time-off allocation remaining) are a **snapshot as of today**, not
  a period aggregate — see §7.K. They still respect `department_id`/`employee_type`.

## 3. Response shape

```jsonc
{
  "filters": { "period_start": "2026-09-01", "period_end": "2026-09-30", "department_id": null, "employee_type": null },
  "kpis": {
    "total_net_salary_paid": "0.00",
    "payslips_generated": 0,
    "average_salary": "0.00",
    "approved_time_off": 0,
    "attendance_health_pct": null
  },
  "salary_cost_by_department": [ { "department_id": "dept_xxx", "department": "Engineering", "amount": "0.00" } ],
  "monthly_net_salary_trend": [ { "month": "2026-09", "amount": "0.00" } ],
  "attendance": {
    "by_status": [ { "status": "present", "count": 0 }, ... ],
    "expected_working_days": 0,
    "attended_days": 0
  },
  "time_off": {
    "pending": 0, "approved": 0, "refused": 0,
    "balance_summary": [ { "time_off_type": "Annual Leave", "time_off_type_id": "tot_xxx", "allocated": "0.00", "taken": "0.00", "remaining": "0.00" } ]
  },
  "warnings": [ { "category": "missing_bank_details", "message": "...", "payslip_id": "pslip_xxx", "employee_id": "emp_xxx", "employee_name": "..." } ],
  "contract_attention": [ { "kind": "expiring", "employee_id": "emp_xxx", "employee_name": "...", "detail": "Contract ends 2026-09-20" } ],
  "department_breakdown": [ { "department_id": "dept_xxx", "department": "Engineering", "headcount": 0, "payroll_spend": "0.00" } ]
}
```

Money is `Decimal` server-side (`Numeric(12,2)` columns, Architecture §10) and travels the
wire the same way every other monetary field in this codebase does — see
`ContractResponse.wage`. No float appears anywhere on this path.

## 4. KPIs — exact definitions

### A. `total_net_salary_paid`
`SUM(Payslip.net_amount)` where `Payslip.status = 'paid'`, joined to `Payrun` for the
period-overlap filter and to `Employee` for `department_id`/`employee_type`. **Never**
reconstructed from wage/Basic/HRA/deductions — `Payslip.net_amount` is the rule engine's
own denormalized authoritative total (`app/models/payroll.py`, "Denormalised totals... 
Written only by the rule engine"). "Paid" means literally `PayslipStatus.PAID` — the
narrowest, least-ambiguous reading of "actually paid."

### B. `payslips_generated`
`COUNT(Payslip)` where `status != 'cancelled'`, same period/employee filters. Counts every
payslip that exists for the period regardless of where it sits in `draft → computed →
validated → paid`, because "generated" describes the row existing (compute created it),
not its finalization state. A cancelled attempt is excluded — it was un-generated by policy.

### C. `average_salary`
**Average paid net salary per employee for the period** — computed as: group `Payslip`
(`status = 'paid'`, period/employee filters) by `employee_id`, sum `net_amount` per
employee, then average that per-employee sum across employees. This (not a flat
`total / payslip_count`) is deliberate: an employee with two paid payslips in the same
window (e.g. a correction re-run) should contribute one number to the average, not two.

### D. `approved_time_off`
`COUNT(TimeOffRequest)` where `status = 'approved'`, `date_from`/`date_to` overlaps the
period, employee filters applied via a join to `Employee`.

### E. `attendance_health_pct`
```
attendance_health_pct = attended_days / expected_working_days * 100
```
- **`expected_working_days`**: for every `Employee` matching the filters (active, and — if
  set — `department_id`/`employee_type`), walk each calendar day in
  `[period_start, period_end]` and count it if the employee's `default_schedule` has at
  least one `ScheduleLine` for that weekday (`WorkingSchedule.lines`, keyed by
  `Weekday`/`WEEKDAY_ORDER` — the exact same schedule shape `app/services/attendance.py`'s
  `schedule_expectations` already reads for the per-day expected-hours check). An employee
  with no `default_schedule` contributes 0 expected days. Summed across every matching
  employee.
- **`attended_days`**: `COUNT(Attendance)` in the period, employee filters applied, where
  `status != 'absent'`. This deliberately does **not** re-derive attendance status — it
  reads the status the Attendance service already computed and stored
  (`derive_attendance_status`), per the instruction not to duplicate Phase 2's logic.
  `missing_checkout` still counts as attended (they checked in), only `absent` (a genuine
  zero-hours record) does not.

This is a real ratio grounded in the schedule, not `present_count / 30`. If
`expected_working_days` is 0 (no matching employee has a schedule, or the filtered set is
empty), the field is `null` rather than a division by zero or a fabricated 0%/100%.

## 5. Charts

### F. `salary_cost_by_department`
`SUM(Payslip.net_amount)` where `status = 'paid'`, period filter, grouped by
`Employee.department_id` (left-joined to `Department` for the name; a null department
renders as `"Unassigned"`). Uses the **employee's current department**, not the contract's
`department_id` snapshot — chosen for consistency with `department_breakdown` (§7.L), which
must answer "headcount + spend" for the same department grouping; splitting the two would
let a bar chart and a headcount table disagree about which department an amount belongs to.

### G. `monthly_net_salary_trend`
Same `SUM(Payslip.net_amount)`/`status = 'paid'` query, grouped by
`to_char(Payrun.period_start, 'YYYY-MM')`. Still scoped to the selected period, per
requirement §5's "Respect the selected period" — a payrun whose `period_start` falls
outside the filtered range does not appear, even in a trend view.

## 6. Operational alerts

### H. `warnings`
Reads `Payslip.warnings` (JSONB) for every payslip in the period/filters, verbatim — no
warning is invented. Each stored entry is normalized into `{category, message}`:
recognized categories are `missing_bank_details`, `duplicate_payslip`, `missing_contract`,
`contract_attention`; anything else (or an entry with no recognizable shape) is passed
through as `category: "other"`, `message: str(entry)`. **Phase 4 has not landed the writer
of this column yet** — see the integration doc; today this returns `[]` on any real
deployment, honestly, rather than fabricating a warning.

### I. `contract_attention`
Two real, schema-supported checks — no Phase 4 dependency:
- **Expiring**: `Contract.status = 'active'`, `end_date` is not null, and
  `today <= end_date <= today + 30 days`. The 30-day horizon is a product choice made here;
  change `CONTRACT_EXPIRY_HORIZON_DAYS` in `app/services/dashboard.py` if a different
  window is wanted.
- **Missing contract**: `Employee.status = 'active'` (employee filters applied) with no
  `Contract` row where `status = 'active'` and `today` falls in `[start_date, end_date]`
  (or `end_date IS NULL`). Only meaningful because of the non-overlap `EXCLUDE` constraint
  guaranteeing at most one such row per employee (Architecture §6) — "missing" and
  "has one" are the only two possible states.

## 7. Overviews

### J. `attendance.by_status`
`GROUP BY Attendance.status, COUNT(*)` within the period/employee filters. Every
`AttendanceStatus` enum value is present in the response with `count: 0` if unused, so a
bar/pie chart never has to special-case a missing key. Reuses the stored `status` column —
no re-derivation (see §4.E).

### K. `time_off.pending` / `.approved` / `.refused`
`COUNT(TimeOffRequest)` grouped by status (`to_approve`, `approved`, `refused`), period
overlap + employee filters, same as §4.D.

### K (continued). `time_off.balance_summary`
**Not period-filtered** — a leave balance is a snapshot ("how much do people have left
right now"), not a flow over a date range, so applying `period_start`/`period_end` to it
would silently answer a different question than the one a balance widget is for. Grouped by
`TimeOffType`, summing `TimeOffAllocation.allocated`/`.taken` (and their difference for
`remaining`) over allocations with `status = 'confirmed'` and currently valid
(`valid_from <= today` and `valid_to IS NULL OR valid_to >= today`).
`department_id`/`employee_type` still apply, via a join to `Employee`.

### L. `department_breakdown`
For every `Department` (plus one `"Unassigned"` row for employees with a null
`department_id`) matching `employee_type`: `headcount = COUNT(Employee)` where
`status = 'active'`, and `payroll_spend` is the same figure as its row in
`salary_cost_by_department` (§5.F) — computed once and shared, so the chart and this table
can never show two different numbers for the same department.

## 8. What is real today vs. what needs Phase 4

The `payroll.py` model file (Payrun/Payslip/PayslipLine) is schema-complete — `net_amount`,
`gross_amount`, `status`, `warnings` all already exist as real columns. What does **not**
exist yet is a way to populate them: `PayrunService`/`PayslipService`
(`app/services/payroll.py`) are still "Phase 0: thin pass-throughs" — there is no compute
endpoint. Every query above is real SQL against real columns; on today's database they
simply return empty results until Phase 4 lands `POST /payruns/{id}/compute`. See
`dashboard-phase4-integration.md` for exactly what Phase 4 needs to preserve for these
queries to start returning real numbers with zero changes here.
