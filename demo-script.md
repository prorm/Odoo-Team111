# PeoplePay360 — Live Demo Script

**5–10 minutes, two end-to-end scenarios (PRD §6).**
Every record, login and figure below was verified live against a from-scratch
rebuild of `main` @ `167dfb9` on 2026-09-06. If a number on screen differs from
a number here, trust the screen and say so — the point of this product is that
the deterministic engine is the authority.

---

## Before you start (2 minutes, do this off-camera)

```bash
docker compose --profile advanced up -d      # or: docker compose up -d  (core only)
```

Wait for `peoplepay360_backend` to report **healthy**, then open
<http://localhost:3000>.

| | |
|---|---|
| Frontend | <http://localhost:3000> |
| API docs | <http://localhost:8000/docs> |
| MailHog (sent payslips) | <http://localhost:8025> |
| MCP server | <http://localhost:8100/mcp> |

**Log in as** `payroll.manager@peoplepay360.com` / `payroll123`
(Payroll Manager — the only role that can see both HR and payroll.)

### Two constraints that are real, not stage directions

1. **Ask the AI assistant ONE question at a time.** Groq is the only AI
   provider and the free tier has an 8000 tokens-per-minute ceiling with no
   fallback. Two questions in quick succession will legitimately return
   *"Temporarily unavailable — try again shortly."* The UI disables the Ask
   button while a question is in flight; let it finish before asking another.
2. **The dashboard defaults to a date range with no paid payroll in it.** The
   seeded paid run is **July 2026**. On the Reports screen you must set
   **From = 01-07-2026, To = 31-07-2026** or every KPI correctly reads 0.00.

---

## Scenario 1 — Employee to payslip, with a real warning (4–5 min)

*The story: a new hire is onboarded, payroll is run, the system refuses to pay
someone it cannot pay, we fix it, and the run completes.*

### 1.1 Show the workforce

- Click **Employees**.
- Point out the seeded roster: **Lakshmi Prasad, Arjun Menon, Divya Rao,
  Kabir Shah, Nisha Gupta** — 5 people across 4 departments.
- Toggle **Kanban / List**. Say: *"same data, two views — Kanban groups by
  employment status."*
- Click **Lakshmi Prasad**. Point at the smart-button counters (contracts,
  attendance, time off). Say: *"these are live counts, not decoration —
  they're what you click to investigate someone."*

### 1.2 Onboard a new hire who is deliberately incomplete

- **Employees → New**. Create:
  - First name **Priya**, Last name **Nair**
  - Work email `priya.nair@peoplepay360.com`
  - Department **Engineering**
  - **Leave Bank account EMPTY.** This is the deliberate break.
- **Contracts → New**: employee **Priya Nair**, start date **2026-01-01**,
  wage **30000**, status **active**, structure **PeoplePay360 Demo Salary
  (PP360_DEMO)**, working schedule **PP360 Demo Full-Time (Mon-Fri)**.

> **The schedule matters.** Without one, payroll can't determine scheduled
> working days and stops earlier with a different error. Set it.

- *(Optional, 20 seconds, shows the headline invariant)* Try to add a **second
  active contract** for Priya starting **2026-06-01**. It is refused with:
  > *"Priya Nair already has an active contract (ctr_…) covering 2026-01-01 to
  > …, which overlaps the requested period. Payroll must resolve exactly one
  > contract per period."*
  Say: *"that's a Postgres exclusion constraint, not application code — the
  database itself will not hold two overlapping active contracts."*

### 1.3 Run payroll and get refused

- **Payroll → New payrun**:
  - Name **August 2026 payroll**
  - Structure **PP360_DEMO**
  - Period **2026-08-01 → 2026-08-31**
  - Select **Priya Nair** and **Lakshmi Prasad**
- Click **Compute**.

**Now point at the Validation Firewall.** It shows:

> **Missing bank details** — *blocking*
> *"Priya Nair has no bank account on file, so this payslip cannot be paid out."*
> **Fix:** *Add a bank account to the employee record, then recompute the payrun.*

- Click **Validate**. It is **refused** — the run cannot advance.

Say: *"The firewall groups findings by cause, tells you the fix, and gates the
transition. It didn't just warn — it stopped the run."*

### 1.4 Fix it and finish

- **Employees → Priya Nair → Edit** → set **Bank account** to `GB00DEMO0001`
  → Save.
- Back on the payrun: **Recompute**. The blocking finding is **gone**;
  the firewall now shows **0 blocking**.
- **Validate** → succeeds. **Mark paid** → status becomes **paid**.
- Click **Send payslips**. Open <http://localhost:8025> — the payslip emails
  are there, with a per-employee delivery status on the payrun screen.
- Click **Print / PDF** on a payslip — a real PDF whose figures match the
  on-screen breakdown line for line.

### 1.5 Show that "paid" means finished

- Still on the paid run, point at **Recompute / Validate / Mark paid** — all
  greyed out, with the banner:
  > *"This run is paid and is preserved as history. Its payslips can no longer
  > be recomputed, edited or deleted — correcting a finalized run means
  > creating a new one."*

Say: *"This isn't a hidden button. The API refuses it too —
`409: a finalized run is never recomputed in place`."*

---

## Scenario 2 — Leave to payroll impact, the Loss-of-Pay case (3–4 min)

*The story: approved unpaid leave changes someone's net pay, and you can trace
exactly why.*

### 2.1 The baseline

- **Payroll → July 2026 payroll (demo)**. It is **paid**, 5 payslips.
- Point at **Lakshmi Prasad**: worked days **23.00**, gross **42,000.00**,
  net **41,800.00**.

Say: *"That's the baseline month — full attendance, no unpaid leave."*

### 2.2 The leave that causes it

- **Time Off**. Show Lakshmi Prasad's **approved unpaid leave,
  2026-08-12 → 2026-08-14** (3 days).
- *(Optional, to show the workflow rather than just the result)* Create a new
  request for any employee, approve it, and show the **balance move** —
  allocated stays, **taken** increases, **remaining** drops. Then refuse a
  different one and show the balance **does not move**.

### 2.3 The payroll impact

- If you already ran the August payrun in Scenario 1, open it. Otherwise create
  one over **2026-08-01 → 2026-08-31** for **Lakshmi Prasad** and **Compute**.
- Open her August payslip. Point at the lines:

| Line | Amount |
|---|---|
| PP360_BASIC | 30,000.00 |
| PP360_HRA | 12,000.00 |
| **PP360_GROSS** | **42,000.00** |
| PP360_PT | 200.00 |
| **PP360_LOP** | **4,285.71** |
| **PP360_NET** | **37,514.29** |

**The three numbers to say out loud:**

- July net **41,800.00** → August net **37,514.29**
- The difference is **4,285.71** — *exactly* the Loss of Pay line
- **Gross did not change** (42,000.00 both months)

Say: *"Loss of pay is a deduction against a deduction-bearing gross, not a cut
to gross. Three unpaid days out of a 21-day schedule on a 30,000 wage produce
4,285.71, and that single line accounts for the entire change in net."*

### 2.4 View Calculation — the audit trail

- Click **View calculation** on that payslip.
- Show the **frozen inputs** (WORKED_DAYS, UNPAID_LEAVE_DAYS, CONTRACT_WAGE)
  next to the **persisted totals**.

Say: *"These are the inputs as they were at compute time, stored with the
payslip. Re-reading this payslip in a year shows the same numbers even if the
contract has changed since."*

---

## Optional closers (30–60 seconds each — pick one or two)

### The AI assistant — grounded, not generative about money

- **Assistant** → question type **Explain a payslip** → paste Lakshmi's July
  payslip id → **Ask**. *(One question. Wait for it.)*
- Real prose comes back from **groq / openai/gpt-oss-120b**, quoting
  **30,000.00 / 42,000.00 / 200.00 / 41,800.00 / 23.00 worked days**.
- Point at the **Evidence** panel beside it.

Say: *"Every figure in that answer is in the evidence panel next to it, and the
evidence came from the payroll engine. The model explains numbers; it never
produces them. There is no code path from the AI layer that can write a
payslip."*

### The payroll dashboard

- **Reports** → set **From 01-07-2026, To 31-07-2026**.
- Total net salary paid **274,800.00**, payslips **5**, average **54,960.00**,
  attendance health **100%**, plus per-department breakdown
  (Engineering 104,600.00, Finance 72,600.00, Sales 53,000.00, HR 44,600.00).

### Server-side RBAC, not hidden menus

- Log out, log in as `hr.manager@peoplepay360.com` / `hrmanager123`.
- The **Payroll** and **Reports** sections are not offered.
- Then say: *"and it isn't just the menu"* — show
  `GET /api/v1/payruns/` returning **403** for that token in the API docs or a
  terminal.

### Realtime

- Open **Time Off** in two browser windows (or two tabs).
- Approve a request in one. The other updates **without a refresh**.

---

## Seed data cheat sheet

| Login | Password | Role |
|---|---|---|
| `admin@peoplepay360.com` | `admin123` | Admin |
| `payroll.manager@peoplepay360.com` | `payroll123` | HR Payroll Manager |
| `payroll.user@peoplepay360.com` | `payroll123` | HR Payroll User |
| `hr.manager@peoplepay360.com` | `hrmanager123` | HR Manager |
| `employee@peoplepay360.com` | `employee123` | Employee (is Lakshmi Prasad) |

| Record | Value |
|---|---|
| LOP employee | **Lakshmi Prasad** `emp_wG2A92xE`, wage 30,000.00 |
| Her approved unpaid leave | 2026-08-12 → 2026-08-14 (3 days) |
| Seeded paid run | **July 2026 payroll (demo)** — 5 payslips, 274,800.00 net |
| Salary structure | **PP360_DEMO**, 6 rules incl. **PP360_LOP** |
| Working schedule | **PP360 Demo Full-Time (Mon-Fri)** |
| August 2026 | **left uncomputed on purpose** — it is the live demo period |

### Resetting between demos

`python -m app.seed` is additive — it will not remove a payrun you created.
For a genuinely clean slate:

```bash
docker compose --profile advanced stop backend worker mcp-server
docker exec peoplepay360_postgres psql -U postgres -c \
  "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname='peoplepay360' AND pid <> pg_backend_pid();"
docker exec peoplepay360_postgres psql -U postgres -c "DROP DATABASE peoplepay360;"
docker exec peoplepay360_postgres psql -U postgres -c "CREATE DATABASE peoplepay360 OWNER postgres;"
docker compose --profile advanced up -d
```

The backend migrates and seeds on startup; this returns exactly the state in
the cheat sheet above.

> Note: `python -m scripts.rbac_audit` is useful but **writes rows** (its
> "own record" probes create an attendance record and a time-off request).
> Run it before a reset, not after.
