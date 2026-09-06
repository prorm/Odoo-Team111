# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

PeoplePay360 serves five role-defined audiences in an operational workplace context:

- Employees view their own profile, attendance, and leave balances; record attendance; and request time off.
- HR Managers maintain employees, contracts, working schedules, attendance, and time off.
- HR Payroll Users run payroll and inspect salary configuration without authoring it.
- HR Payroll Managers author salary rules and structures and run payroll end to end.
- Administrators have full system access, including user and role administration.

## Product Purpose

PeoplePay360 connects employee records, period-valid contracts, schedules, attendance, approved leave, deterministic salary rules, payruns, payslips, delivery, and reporting in one auditable HR and payroll system. Success means an authorized operator can complete the full employee-to-payslip workflow without manual database intervention and can trace every payroll amount to its source records and rule.

## Positioning

The product's core mechanism is one deterministic business layer shared by the human UI, automation, offline sync, AI explanations, and MCP tools. AI may explain or propose, but it never calculates payroll or bypasses validation, authorization, or audit.

## Operating Context

The product is used as a professional ERP workspace for recurring HR administration and payroll operations. Work includes scanning dense record lists, editing structured forms, resolving exceptions, approving requests, progressing payruns through controlled states, investigating warnings, comparing historical calculations, generating payslip PDFs, and sending bulk email. Payroll history must remain legible and trustworthy after later master-data changes.

## Capabilities and Constraints

- Existing routes, workflows, forms, server-side RBAC, API calls, validation behavior, state transitions, and data contracts are functional requirements and must be preserved during visual work.
- Payroll uses ordered salary rules and decimal arithmetic; formulas accept restricted arithmetic only.
- Finalized payroll is immutable history. Warnings and blocking findings must remain conspicuous and actionable.
- Offline mutation is limited to attendance and time-off creation. Payroll mutations are never offline-capable.
- Realtime and AI features are additive views over authoritative application state.
- The product is currently single-tenant and single-currency.

## Brand Commitments

- Product name: PeoplePay360.
- The interface must look and behave like a credible professional ERP product, not a generic AI-generated dashboard.
- Visual expression must support sustained daily operation: precise, disciplined, information-dense, and calm under complex data.
- Avoid decorative gradients, glassmorphism, gratuitous glow, floating card grids, novelty typography, and ornamental metrics that do not improve a task.

## Evidence on Hand

- `01_PRD.md` is the source of truth for product scope, roles, workflows, and success criteria.
- `02_SYSTEM_ARCHITECTURE.md` and `HARMONIX360_ARCHITECTURE.md` document system constraints and security boundaries.
- The React frontend under `harmonix360/frontend/src` contains the working routes and interactions that the redesign must preserve.
- Seeded demo data and the backend test suite exercise the required HR/payroll scenarios. No customer logos, testimonials, commercial benchmarks, or external brand assets are supplied; visual work must not fabricate them.

## Product Principles

1. Business state is visible before visual decoration.
2. Every action respects the same authorization, validation, and audit boundaries.
3. Payroll numbers are deterministic, traceable, and historically stable.
4. Dense workflows remain scannable for frequent operators and understandable for occasional users.
5. Advanced intelligence explains authoritative records; it does not replace them.

## Accessibility & Inclusion

Preserve keyboard operability, visible focus, semantic labels, readable error states, reduced-motion compatibility, and WCAG AA contrast for operational text and controls across desktop and mobile web layouts.
