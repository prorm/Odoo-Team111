# Demo screenshot fallback pack

Captured 2026-09-06 in a real Chromium session against `main` @ `167dfb9`,
running on a from-scratch rebuild (all images deleted, `--no-cache`, database
volume destroyed and reseeded).

**Purpose:** if the live environment or internet fails during an evaluation,
these show what each step of [demo-script.md](../../demo-script.md) is supposed
to look like. They are not a substitute for the live demo.

| File | Step in the demo script | What to point at |
|---|---|---|
| `D01-login.png` | Before you start | The five seeded logins work as documented |
| `D02-employees.png` | 1.1 | Seeded roster, 5 people across 4 departments |
| `D03-employee-lakshmi.png` | 1.1 | Detail form + smart-button counters |
| `D04-contracts.png` | 1.2 | Contract list |
| `D05-attendance.png` | 2.2 | Attendance records and statuses |
| `D06-time-off.png` | 2.2 | Lakshmi's approved unpaid leave 2026-08-12→14 |
| `D07-payroll-list.png` | 2.1 | The seeded paid July run |
| `D08-dashboard.png` | closer | **Shows the default empty period** — the reason the script tells you to set From/To to July 2026 |
| `D09-anomalies.png` | closer | Deterministic anomaly signals |
| `D10-assistant.png` | closer | Answer + Evidence split before asking |
| `D11-july-payrun-immutable.png` | 1.5 / 2.1 | Recompute/Validate/Mark-paid greyed out on a paid run, the history banner, and **Lakshmi Prasad 42,000.00 → 41,800.00** |
| `D12-assistant-answer-full.png` | closer | Real Groq prose beside the evidence panel (full-page capture, very tall) |

Screens created *during* the demo — the August payrun, the
`missing_bank_details` firewall finding, and the LOP payslip — are deliberately
not pre-captured: they only exist once you have run Scenario 1, and the demo
database ships in its pristine seeded state.
