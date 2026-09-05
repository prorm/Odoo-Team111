# PeoplePay360 — Roadmap

> **SKELETON ONLY — DO NOT READ AS STATUS.**
>
> This file is structure and section headers, drafted during Phase 7
> preparation. Sections marked `<!-- TO BE FILLED -->` are empty because the
> work they describe **has not been done yet**, not because nobody got round to
> writing them up. Filling one in before its phase lands would turn this file
> into the thing a roadmap must never be: a document that says a feature exists
> because someone wrote a sentence about it.
>
> The authoritative record of what is actually built is `progress.md`, by dated
> section. Where this file and `progress.md` disagree, `progress.md` wins and
> this file is stale.
>
> Drafted against `origin/dev` at `cd823a17799c7d651066a51053e8db3e296ff3a2`.

---

## How to read this file

| Marker | Meaning |
|---|---|
| **DONE** | Landed on `origin/dev`, with a dated `progress.md` section and passing tests |
| **IN FLIGHT** | A branch exists and is not integrated |
| **NOT STARTED** | No code, no branch |
| `<!-- TO BE FILLED -->` | Section deliberately empty pending the phase's completion |

Every "DONE" claim below must be checkable against a `progress.md` section and
a commit on `origin/dev`. If it is not, it is wrong.

---

## 1. Scope and source of truth

- **Problem statement:** Odoo Hackathon PS — PeoplePay360 HR & Payroll.
- **Requirements:** `01_PRD.md` §4 (A1–A7, B1–B9) — all mandatory.
- **Architecture:** `02_SYSTEM_ARCHITECTURE.md`.
- **Build log:** `progress.md` (dated sections, newest last).

---

## 2. Delivered — core HR (Phases 0–2)

**Status: DONE.**

### 2.1 Phase 0 — domain skeleton and RBAC
### 2.2 Phase 1 — Employee, Working Schedule, Contract
### 2.3 Phase 2 — Attendance and Time Off

<!-- Summaries to be lifted from progress.md's Phase 0-2 sections. Content
     exists; condensing it is a writing task, not a status question. -->

---

## 3. Delivered — payroll engine (Phases 3–4, Gap-fix)

**Status: DONE.**

### 3.1 Phase 3 — Salary Structures and Rules
### 3.2 Phase 4 — Payrun, Payslip, the deterministic engine
### 3.3 Gap-fix — Loss of Pay, and the historical-snapshot bug

<!-- Summaries to be lifted from progress.md. Note when filling in: the LOP
     formula, the snapshot/409 migration limitation, and the legacy-payslip
     caveat are the three things a reader most needs from this section. -->

---

## 4. Delivered — reporting (Phase 6)

**Status: DONE.** Note the out-of-order numbering: Phase 6 was built in
parallel with Phase 4 and integrated after it. That is real history and is left
visible rather than renumbered.

### 4.1 Phase 6 — Payroll Dashboard
### 4.2 Phase 4/6 integration — warning-shape mismatch

<!-- TO BE FILLED -->

---

## 5. Phase 5 — Payslip PDF and bulk email (PS B8)

**Status: IN FLIGHT.** Branch `origin/phase-5-payslip-pdf-email` exists and is
**not integrated into `dev`**. Nothing in this section may be marked DONE until
it is merged and `progress.md` carries its dated section.

### 5.1 What exists today (the boundary Phase 4 left)

`POST /api/v1/payruns/{id}/send-payslips` validates that the run is PAID,
builds a JSON-safe payload, and kicks a Taskiq task **by name**:

```
SEND_PAYSLIPS_TASK_NAME = "send_payslips"
payload = {payrun_id, payrun_name, period_start, period_end,
           payslips: [{payslip_id, employee_id, work_email,
                       net_amount, gross_amount}]}   # amounts are STRINGS
```

No worker consumes it. No PDF renderer exists in the tree.

### 5.2 What Phase 5 must not do

Two constraints inherited from the gap-fix phase, recorded here because they
are easy to violate and expensive to discover late:

- The PDF worker must render from **`payslip_response` / the stored
  `reference_snapshot`**, never from live Contract or Employee rows. A payslip
  reprinted after a raise must show the wage it was computed at.
- Legacy payslips without a `reference_snapshot` return 409
  `historical_snapshot_unavailable`. The renderer must surface that, not
  fabricate a document from live data.

### 5.3 Delivery status
### 5.4 Open questions

<!-- TO BE FILLED — requires Phase 5 to land. -->

---

## 6. Phase 7 — Hardening, RBAC audit, demo readiness

**Status: NOT STARTED** (preparation only).

### 6.1 Preparation done so far

- `docs/rbac-audit-partial.md` — dashboard routes verified by real API calls
  per role; Print/Send Payslip rows marked pending Phase 5.
- Demo seed extended with the Loss-of-Pay scenario (`app/seed.py`).
- Adversarial/failure-mode regression suite (branch `phase-11-prefix-tests`).

### 6.2 Full RBAC audit — all modules, all verbs
### 6.3 Demo dataset completeness
### 6.4 Performance and load characteristics
### 6.5 Known limitations to disclose

<!-- TO BE FILLED — Phase 7 proper is a separate gated task. Note when filling
     in 6.3: "a paid payrun with a generated PDF payslip" is the one demo item
     that cannot be completed until Phase 5 lands. -->

---

## 7. Phase 8 — Offline sync

**Status: NOT STARTED.** The engine is built and dormant; the registry
(`app/services/sync_entities.py`) is deliberately empty. Architecture §8.3
scopes it to `attendance` (check-in/out only, never corrections) and
`time_off_request` (create only). Payroll is never registered.

### 7.1 Entities to register
### 7.2 Conflict policy
### 7.3 Client outbox behaviour

<!-- TO BE FILLED -->

---

## 8. Phase 9 — AI and MCP

**Status: NOT STARTED.** The layer is present and dormant. The invariant that
governs the whole phase, from Architecture §7/§10: **AI is architecturally
incapable of writing to `Payslip`/`PayslipLine`.** It may read and narrate; the
deterministic rule engine is the sole author of every figure on a payslip.

### 8.1 Explainability (PRD §5.6)
### 8.2 MCP tools and their role binding
### 8.3 What AI must never be allowed to do

<!-- TO BE FILLED -->

---

## 9. Phase 10 — Realtime and observability

**Status: NOT STARTED.** WebSocket/SSE presentation over committed DB state;
REST stays authoritative (PRD §5, "Optional WebSocket/SSE presentation layer").

### 9.1 Channels
### 9.2 Telemetry and error reporting

<!-- TO BE FILLED -->

---

## 10. Cross-cutting invariants

These hold across every phase above and are not negotiable per-phase. Listed
here so a roadmap reader meets them once rather than rediscovering them.

1. **Money is `Decimal`/`Numeric(12,2)` end to end**, stringified across every
   JSON boundary. No `float` on the payroll path (Architecture §10).
2. **The deterministic rule engine is the sole author of payslip figures.**
   No second implementation of salary arithmetic, anywhere.
3. **Exactly one active contract per employee per period**, enforced by a
   Postgres EXCLUDE constraint, not by application code.
4. **`require_role` is the single permission gate**, identical for REST, MCP
   and offline sync.
5. **Finalized payruns are history.** Validated/paid runs are never recomputed,
   edited or deleted in place.
6. **Payslips render from their snapshot**, never from live contract data.

---

## 11. Known gaps carried forward

<!-- TO BE FILLED. Seed the list from progress.md's "follow-ups that must stay
     explicit" paragraphs; the point of this section is that a gap someone
     decided not to close stays visible instead of being forgotten. -->
