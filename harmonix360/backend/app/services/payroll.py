"""Payroll services — Payrun and Payslip (PS B5/B6/B7, Architecture §6/§7).

This module owns the transaction, the lock and the writes. It owns no
arithmetic: every figure on every payslip comes from
`resolve_salary_structure` (Phase 3, app/services/salary_resolver.py), called
directly and unmodified. There is no second implementation of fixed /
percentage / formula anywhere in this phase, and there must never be one — two
implementations of payroll mathematics is two answers to "what were they
paid", and the divergence shows up first as a rounding cent and later as a
tribunal.

What each of the three §6 obligations means here, concretely:

  * **`acquire_entity_lock(session, "payrun", payrun_id)` wraps Compute.**
    Compute deletes and rewrites the run's ENTIRE payslip set. That is a
    read-compute-write over a parent-scoped collection, which no per-row
    constraint can protect: two concurrent computes would interleave into a
    payslip set neither run intended, with every individual row still
    perfectly valid. The lock is taken before anything this transaction
    intends to rewrite is read (app/core/locks.py's usage note).

  * **`Idempotency-Key` is REQUIRED on Payrun create and compute.** Enforced
    at the router (`require_idempotency_key`), replayed by
    `IdempotencyMiddleware`. It is what turns a double-clicked Compute into
    one answer rather than two runs of the engine.

  * **Money stays `Decimal` end to end**, `Numeric(12,2)` in the database, and
    is stringified across any JSON boundary. No `float` is constructed
    anywhere in this file.

FINALITY
--------
A payrun's status is a one-way ratchet: DRAFT → COMPUTED → VALIDATED → PAID.
Compute is refused once a run is VALIDATED or PAID, and so is editing or
deleting it or any of its payslips. "Finalized runs preserved as history"
(PS B6) is not a UI convention — it is enforced in `_assert_recomputable` and
`_assert_payslips_mutable`, so a direct service call (an MCP tool, a script)
hits the same wall an HTTP request does.

AI has no write path here. Nothing in app/ai or app/mcp may call a mutating
method in this module; the rule engine is the sole author of every figure on a
payslip (Architecture §7/§10).
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional, Sequence, Tuple

from fastapi import HTTPException, status
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.orm.exc import StaleDataError

from app.core.locks import acquire_entity_lock
from app.models.contract import Contract
from app.models.employee import Employee
from app.models.enums import ContractStatus, PayrunStatus, PayslipStatus, SalaryRuleCategory
from app.models.payroll import Payrun, PayrunEmployee, Payslip, PayslipLine
from app.models.salary import SalaryStructure
from app.repositories.hr import (
    EmployeeRepository,
    PayrunEmployeeRepository,
    PayrunRepository,
    PayslipLineRepository,
    PayslipRepository,
    SalaryStructureRepository,
)
from app.services.base import BaseService
from app.services.payroll_context import (
    BLOCKING,
    ContractResolutionError,
    PayrollContextError,
    blocking_summary,
    build_payroll_context,
    overlapping_payslip_ids,
    resolve_period_contract,
    warning_checks,
)
from app.services.payslip_snapshot import capture_references, payslip_response
from app.services.salary_resolver import (
    FormulaEvaluationError,
    ResolvedRule,
    resolve_salary_structure,
    validate_formula_syntax,
)

logger = logging.getLogger("harmonix360.services.payroll")

TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0.00")

#: Statuses a run can still be computed from. VALIDATED and PAID are absent
#: deliberately — see FINALITY in the module docstring.
RECOMPUTABLE = frozenset({PayrunStatus.DRAFT, PayrunStatus.COMPUTED})

#: Everything a PayrunResponse renders, loaded eagerly so Pydantic's
#: synchronous attribute access never triggers a lazy load inside an async
#: request (the MissingGreenlet trap ContractService._reload documents).
_PAYRUN_LOADS = (
    selectinload(Payrun.salary_structure),
    selectinload(Payrun.selected_employees).selectinload(PayrunEmployee.employee),
)

_PAYSLIP_LOADS = (
    selectinload(Payslip.employee),
    selectinload(Payslip.contract),
    selectinload(Payslip.lines),
    selectinload(Payslip.payrun),
)


class PayrunService(BaseService[Payrun]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, PayrunRepository(session), entity_name="Payrun")
        self.structure_repo = SalaryStructureRepository(session)
        self.employee_repo = EmployeeRepository(session)
        self.selection_repo = PayrunEmployeeRepository(session)
        self.payslip_repo = PayslipRepository(session)
        self.line_repo = PayslipLineRepository(session)

    # ------------------------------------------------------------------ reads

    async def list_payruns(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        payrun_status: Optional[PayrunStatus] = None,
        tenant_id: str = "default",
    ) -> Tuple[List[Payrun], int]:
        conditions = [Payrun.tenant_id == tenant_id, Payrun.deleted_at.is_(None)]
        if payrun_status:
            conditions.append(Payrun.status == payrun_status)

        total = (await self.session.execute(select(func.count(Payrun.id)).where(*conditions))).scalar() or 0
        stmt = (
            select(Payrun)
            .where(*conditions)
            # Most recent period first: a payrun list is read to answer "what
            # is running now", and the answer is at the top.
            .order_by(Payrun.period_start.desc(), Payrun.id.desc())
            .options(*_PAYRUN_LOADS)
            .limit(limit)
            .offset(offset)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, total

    async def read_payrun(self, public_id: str) -> Payrun:
        payrun = await self.get_or_404(public_id)
        return await self._reload(payrun.id)

    async def payslip_counts(self, payruns: Sequence[Payrun]) -> dict[int, int]:
        """Payslips per payrun for a whole page in one grouped query, rather
        than a COUNT per row — same reasoning as
        `SalaryStructureService.contract_usage_counts`."""
        ids = [p.id for p in payruns]
        if not ids:
            return {}
        stmt = (
            select(Payslip.payrun_id, func.count(Payslip.id))
            .where(Payslip.payrun_id.in_(ids), Payslip.deleted_at.is_(None))
            .group_by(Payslip.payrun_id)
        )
        return {payrun_id: count for payrun_id, count in (await self.session.execute(stmt)).all()}

    async def eligible_employees(
        self, period_start: date, period_end: date, *, limit: int = 200, offset: int = 0
    ) -> Tuple[List[tuple[Employee, Contract]], int]:
        """Employees the engine could actually pay for this period (PS B5
        step 2's candidate list).

        The predicate is the same one Architecture §7 step 1 uses at compute
        time — an active, undeleted contract overlapping the period — so the
        wizard offers exactly the people Compute can produce a payslip for.
        Offering every active employee instead would defer the disappointment
        to Compute, which is the worst moment to discover a leaver has no
        contract.

        The Contract non-overlap constraint guarantees at most one active
        contract per employee per period, so this join cannot duplicate an
        employee.
        """
        conditions = [
            Contract.status == ContractStatus.ACTIVE,
            Contract.deleted_at.is_(None),
            Contract.start_date <= period_end,
            or_(Contract.end_date.is_(None), Contract.end_date >= period_start),
            Employee.deleted_at.is_(None),
        ]
        total = (
            await self.session.execute(
                select(func.count(Contract.id)).join(Employee, Contract.employee_id == Employee.id).where(*conditions)
            )
        ).scalar() or 0

        stmt = (
            select(Employee, Contract)
            .join(Contract, Contract.employee_id == Employee.id)
            .where(*conditions)
            .order_by(Employee.last_name, Employee.first_name, Employee.id)
            .limit(limit)
            .offset(offset)
        )
        rows = [(employee, contract) for employee, contract in (await self.session.execute(stmt)).all()]
        return rows, total

    # -------------------------------------------------------------- mutations

    async def create_payrun(self, dto, *, actor_email: str) -> Payrun:
        structure = await self.structure_repo.get_by_public_id(dto.salary_structure_id)
        if structure is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Salary structure '{dto.salary_structure_id}' not found",
            )

        employees = await self._resolve_employees(dto.employee_ids)

        payrun = Payrun(
            public_id="temp",
            name=dto.name,
            salary_structure_id=structure.id,
            period_start=dto.period_start,
            period_end=dto.period_end,
            notes=dto.notes,
            status=PayrunStatus.DRAFT,
        )
        created = await self.create(
            payrun,
            actor=actor_email,
            action="CREATE_PAYRUN",
            after_diff={
                "structure": structure.public_id,
                "period_start": str(dto.period_start),
                "period_end": str(dto.period_end),
                "selected_employees": len(employees),
            },
        )
        await self._replace_selection(created, employees)
        return await self._reload(created.id)

    async def update_payrun(self, public_id: str, dto, *, actor_email: str) -> Payrun:
        payrun = await self.get_or_404(public_id)
        self._assert_version(payrun, dto.version)
        self._assert_editable(payrun)

        before = {"name": payrun.name, "selected_employees": len(payrun.selected_employees)}
        payrun.name = dto.name
        payrun.notes = dto.notes

        employees = None
        if dto.employee_ids is not None:
            employees = await self._resolve_employees(dto.employee_ids)

        updated = await self.update(
            payrun,
            actor=actor_email,
            action="UPDATE_PAYRUN",
            before_diff=before,
            after_diff={
                "name": dto.name,
                "selected_employees": len(employees) if employees is not None else before["selected_employees"],
            },
        )
        if employees is not None:
            await self._replace_selection(updated, employees)
        return await self._reload(updated.id)

    async def delete_payrun(self, public_id: str, version: int, *, actor_email: str) -> Payrun:
        payrun = await self.get_or_404(public_id)
        self._assert_version(payrun, version)
        if payrun.status in (PayrunStatus.VALIDATED, PayrunStatus.PAID):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Payrun {payrun.public_id} is {payrun.status.value} and is preserved as history "
                    "(PS B6). A finalized run cannot be deleted."
                ),
            )
        return await self.soft_delete(payrun, actor=actor_email, action="DELETE_PAYRUN")

    # ----------------------------------------------------------------- compute

    async def compute(
        self, public_id: str, version: int, *, actor_email: str, now: Optional[datetime] = None
    ) -> dict:
        """Architecture §7, steps 1-6, for every selected employee.

        Ordering inside this method is load-bearing:

        1. Resolve the public id to an internal id — the smallest possible
           read, just enough to name the lock.
        2. Take the advisory lock. Everything this transaction rewrites is
           read AFTER this point; taking it later would mean acting on a read
           the other writer has already invalidated.
        3. Re-read the payrun with `populate_existing`, so the status and
           version checked are the ones that survived the wait, not the ones
           this session happened to have cached before it blocked.

        The version check comes after the lock for the same reason: a compute
        that queued behind another compute has, by definition, a stale read,
        and it must be told so rather than quietly rewriting the winner's
        payslips.
        """
        payrun = await self.get_or_404(public_id)

        await acquire_entity_lock(self.session, "payrun", payrun.id)
        payrun = await self._reload(payrun.id)

        self._assert_version(payrun, version)
        self._assert_recomputable(payrun)

        structure = await self._load_structure(payrun.salary_structure_id)
        active_links = [link for link in structure.rule_links if link.salary_rule.is_active]
        if not active_links:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Salary structure '{structure.code}' has no active salary rules, so this payrun "
                    "would produce empty payslips for everyone in it. Add rules to the structure, or "
                    "run a different one."
                ),
            )
        # code -> (rule id, name, this structure's sequence for it). Built from
        # the same links the resolver walks, so a line's `sequence` is the
        # position the rule actually ran at in THIS structure, not the rule's
        # own default.
        rule_meta = {
            link.salary_rule.code: (link.salary_rule.id, link.salary_rule.name, link.sequence)
            for link in active_links
        }

        await self._delete_payslips(payrun)

        computed: list[Payslip] = []
        skipped: list[dict] = []
        computation_warnings: list[dict] = []

        for selection in payrun.selected_employees:
            employee = selection.employee
            try:
                contract = await resolve_period_contract(
                    self.session, employee, payrun.period_start, payrun.period_end
                )
            except ContractResolutionError as exc:
                # NOT fatal to the run. One leaver without a contract must not
                # stop everyone else being paid; the omission is reported here
                # and re-derived by Validate, which refuses to finalize a run
                # whose selection is not fully covered.
                skipped.append(
                    {
                        "employee_id": employee.public_id,
                        "employee_name": employee.full_name,
                        "reason": exc.reason,
                    }
                )
                continue

            try:
                payslip = await self._compute_one(
                    payrun=payrun, employee=employee, contract=contract,
                    structure=structure, rule_meta=rule_meta, now=now,
                )
            except PayrollContextError as exc:
                computation_warnings.append({**exc.warning.as_dict(), "employee_id": employee.public_id})
                skipped.append({"employee_id": employee.public_id, "employee_name": employee.full_name, "reason": str(exc)})
                continue
            computed.append(payslip)

        payrun.computation_warnings = computation_warnings
        payrun.status = PayrunStatus.COMPUTED
        # A recompute replaces every payslip in the run, which is a material
        # change even when the status letter does not move (COMPUTED ->
        # COMPUTED). Forcing the UPDATE bumps `version`, so a client holding
        # the pre-recompute read is correctly told it is stale instead of
        # being allowed to act on payslips that no longer exist.
        flag_modified(payrun, "status")
        await self._flush_or_conflict()

        blocking = self._aggregate_blocking(computed)
        for finding in computation_warnings:
            blocking[finding["code"]] = blocking.get(finding["code"], 0) + 1
        await self.audit(
            actor_email,
            "COMPUTE_PAYRUN",
            payrun.public_id,
            after_diff={
                "status": payrun.status.value,
                "payslips": len(computed),
                "skipped": len(skipped),
                "blocking_issues": blocking,
            },
        )

        return {
            "payrun": await self._reload(payrun.id),
            "computed": computed,
            "skipped": skipped,
            "blocking": blocking,
        }

    async def _compute_one(
        self,
        *,
        payrun: Payrun,
        employee: Employee,
        contract: Contract,
        structure: SalaryStructure,
        rule_meta: dict[str, tuple[int, str, int]],
        now: Optional[datetime],
    ) -> Payslip:
        """One employee's payslip: context, resolver, lines, warnings."""
        # Step 3 — the computation context, from Attendance + approved Time Off.
        context = await build_payroll_context(
            self.session, employee, contract, payrun.period_start, payrun.period_end, now=now
        )
        if context.lop_warning:
            for link in structure.rule_links:
                rule = link.salary_rule
                if rule.is_active and (
                    rule.percentage_base_code == "LOP_AMOUNT"
                    or (rule.expression and "LOP_AMOUNT" in validate_formula_syntax(rule.expression))
                ):
                    raise PayrollContextError(context.lop_warning)

        # Step 4 — THE Phase 3 resolver. Not reimplemented, not wrapped in
        # arithmetic of our own: the list it returns is the payslip.
        try:
            resolved = resolve_salary_structure(structure, context.seed)
        except FormulaEvaluationError as exc:
            # A well-formed structure that cannot evaluate against THIS
            # employee's inputs (a divide by zero on zero worked days, say).
            # `validate_structure_rule_order` already refused malformed
            # structures at save time, so reaching here means the data, not
            # the authoring, is the problem — and computing a partial payslip
            # from a half-evaluated structure would be worse than refusing.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Salary structure '{structure.code}' could not be evaluated for "
                    f"{employee.full_name}: {exc}"
                ),
            ) from exc

        # Steps 5 — categorize into Basic/Allowances/Gross/Deductions/Net.
        gross, net = self._totals(resolved)

        # Step 6 — the deterministic warning checks.
        duplicates = await overlapping_payslip_ids(
            self.session,
            employee,
            payrun.period_start,
            payrun.period_end,
            exclude_payrun_id=payrun.id,
        )
        warnings = warning_checks(
            employee=employee,
            contract=contract,
            payrun=payrun,
            context=context,
            duplicate_payslip_ids=duplicates,
        )

        payslip = Payslip(
            public_id="temp",
            payrun_id=payrun.id,
            employee_id=employee.id,
            # Stored, never resolved on read: a payslip computed in March must
            # keep showing March's contract after it is superseded
            # (app/models/payroll.py, Architecture §5.8's time machine).
            contract_id=contract.id,
            reference_snapshot=capture_references(employee, contract, payrun),
            context_snapshot={key: str(value) for key, value in context.seed.items()},
            worked_days=context.attendance.worked_days,
            gross_amount=gross,
            net_amount=net,
            status=PayslipStatus.COMPUTED,
            warnings=[w.as_dict() for w in warnings],
        )
        try:
            await self.payslip_repo.create(payslip)
        except IntegrityError as exc:
            # uq_payslip_payrun_employee — the database half of "a duplicate
            # payslip attempt is caught, not silently duplicated". Reachable
            # only if the same employee is somehow selected twice; the schema
            # forbids that too (uq_payrun_employee_unique), so this is the
            # backstop for both.
            await self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"{employee.full_name} already has a payslip in this payrun. "
                    "One employee gets one payslip per run."
                ),
            ) from exc

        for resolved_rule in resolved:
            rule_id, rule_name, sequence = rule_meta[resolved_rule.code]
            await self.line_repo.create(
                PayslipLine(
                    public_id="temp",
                    payslip_id=payslip.id,
                    salary_rule_id=rule_id,
                    # COPIED from the rule, not referenced through it: a
                    # payslip must keep rendering the rule as it was when it
                    # ran (app/models/payroll.py).
                    code=resolved_rule.code,
                    name=rule_name,
                    category=resolved_rule.category,
                    sequence=sequence,
                    amount=resolved_rule.amount,
                )
            )
        await self.session.flush()
        return payslip

    @staticmethod
    def _totals(resolved: Sequence[ResolvedRule]) -> tuple[Decimal, Decimal]:
        """Architecture §7 step 5 — the two denormalised totals on a payslip.

        A structure that declares GROSS and NET rules is taken at its word:
        those categories exist precisely to name the run's totals, and the
        LAST one wins so a structure that recomputes a total after a late
        adjustment reports the final figure rather than the intermediate one.

        The fallback exists because both columns are NOT NULL and a structure
        is not obliged to declare either category. It is a derivation, not an
        invention of new rules: gross is what was earned (BASIC + ALLOWANCE)
        and net is what is left after DEDUCTION. It never overrides a
        structure that states its own totals, and a structure that states them
        is the normal case.
        """
        by_category: dict[SalaryRuleCategory, list[Decimal]] = {}
        for rule in resolved:
            by_category.setdefault(rule.category, []).append(rule.amount)

        declared_gross = by_category.get(SalaryRuleCategory.GROSS)
        declared_net = by_category.get(SalaryRuleCategory.NET)

        if declared_gross:
            gross = declared_gross[-1]
        else:
            gross = sum(
                by_category.get(SalaryRuleCategory.BASIC, []) + by_category.get(SalaryRuleCategory.ALLOWANCE, []),
                ZERO,
            )

        if declared_net:
            net = declared_net[-1]
        else:
            net = gross - sum(by_category.get(SalaryRuleCategory.DEDUCTION, []), ZERO)

        return gross.quantize(TWO_PLACES), net.quantize(TWO_PLACES)

    # ---------------------------------------------------------- validate/pay

    async def validation_report(self, payrun: Payrun) -> dict:
        """PRD §5.10's firewall, read-only — the "Revalidate" action.

        Every issue is re-derived from persisted state: the payslips' stored
        `warnings`, plus the selected employees who have no payslip at all.
        Nothing is cached, so pressing Revalidate after fixing a record gives
        the answer the fix produced rather than the answer the last Compute
        recorded.
        """
        payslips = await self._payslips_of(payrun)
        issues: list[dict] = []
        blocking = 0
        advisory = 0
        by_code: dict[str, int] = {}
        for finding in payrun.computation_warnings or []:
            issues.append({**finding, "payslip_id": None})
            blocking += 1
            by_code[finding["code"]] = by_code.get(finding["code"], 0) + 1

        for payslip in payslips:
            if payslip.reference_snapshot is None:
                issues.append({
                    "code": "historical_snapshot_unavailable", "severity": BLOCKING,
                    "message": "Legacy payslip has no reference snapshot; recompute before finalization.",
                    "references": [payslip.public_id], "payslip_id": payslip.public_id,
                    "employee_id": payslip.employee.public_id,
                })
                blocking += 1
                by_code["historical_snapshot_unavailable"] = by_code.get("historical_snapshot_unavailable", 0) + 1
            for entry in payslip.warnings or []:
                issues.append(
                    {
                        **entry,
                        "payslip_id": payslip.public_id,
                        "employee_id": payslip.employee.public_id,
                    }
                )
                if entry.get("severity") == BLOCKING:
                    blocking += 1
                    by_code[entry["code"]] = by_code.get(entry["code"], 0) + 1
                else:
                    advisory += 1

        # A selected employee with no payslip is a blocking issue that no
        # payslip can carry, because there is no payslip. Derived from the two
        # persisted collections rather than from Compute's return value, so it
        # stays true however long ago Compute ran.
        with_payslip = {payslip.employee_id for payslip in payslips}
        for selection in payrun.selected_employees:
            if selection.employee_id in with_payslip:
                continue
            issues.append(
                {
                    "code": "no_payslip",
                    "severity": BLOCKING,
                    "message": (
                        f"{selection.employee.full_name} is selected into this payrun but has no "
                        "payslip — payroll could not resolve a required contract or computation input."
                    ),
                    "references": [selection.employee.public_id],
                    "payslip_id": None,
                    "employee_id": selection.employee.public_id,
                }
            )
            blocking += 1
            by_code["no_payslip"] = by_code.get("no_payslip", 0) + 1

        return {
            "payrun_id": payrun.public_id,
            "status": payrun.status,
            "blocking_count": blocking,
            "advisory_count": advisory,
            "blocking_by_code": by_code,
            "issues": issues,
        }

    async def validate_payrun(self, public_id: str, version: int, *, actor_email: str) -> dict:
        """PS B6's Validate, gated by PRD §5.10's firewall.

        Refuses with a 409 whose body IS the validation report while any
        blocking issue stands. That is the point of the gate: the run does not
        become finalizable because someone pressed the button again, only
        because the offending records were fixed and Compute was re-run.
        """
        payrun = await self.get_or_404(public_id)
        payrun = await self._reload(payrun.id)
        self._assert_version(payrun, version)

        if payrun.status != PayrunStatus.COMPUTED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Only a computed payrun can be validated; {payrun.public_id} is "
                    f"{payrun.status.value}."
                ),
            )

        report = await self.validation_report(payrun)
        if report["blocking_count"]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": (
                        f"{report['blocking_count']} blocking issue(s) must be resolved before this "
                        "payrun can be validated. Fix the offending records and recompute."
                    ),
                    "report": _jsonable_report(report),
                },
            )

        payrun.status = PayrunStatus.VALIDATED
        for payslip in await self._payslips_of(payrun):
            payslip.status = PayslipStatus.VALIDATED
        await self._flush_or_conflict()
        await self.audit(
            actor_email,
            "VALIDATE_PAYRUN",
            payrun.public_id,
            after_diff={"status": payrun.status.value, "advisory_issues": report["advisory_count"]},
        )
        return {"payrun": await self._reload(payrun.id), "report": report}

    async def mark_paid(self, public_id: str, version: int, *, actor_email: str) -> Payrun:
        """PS B6's Mark Paid — the point of no return.

        After this, the run and every payslip in it are immutable: no
        recompute, no edit, no delete. That is enforced here and in
        `_assert_recomputable` / `_assert_payslips_mutable`, not in the UI,
        because a payslip that changes after it has been paid is a payslip
        that no longer matches the money that left the account.
        """
        payrun = await self.get_or_404(public_id)
        payrun = await self._reload(payrun.id)
        self._assert_version(payrun, version)

        if payrun.status != PayrunStatus.VALIDATED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Only a validated payrun can be marked paid; {payrun.public_id} is "
                    f"{payrun.status.value}. Validate it first — that is the step that runs the "
                    "blocking-issue checks."
                ),
            )

        payrun.status = PayrunStatus.PAID
        for payslip in await self._payslips_of(payrun):
            payslip.status = PayslipStatus.PAID
        await self._flush_or_conflict()
        await self.audit(
            actor_email, "MARK_PAYRUN_PAID", payrun.public_id, after_diff={"status": payrun.status.value}
        )
        return await self._reload(payrun.id)

    async def enqueue_payslip_delivery(self, public_id: str, *, actor_email: str) -> dict:
        """PS B6's Send Payslips — THE ENQUEUE BOUNDARY, AND NOTHING PAST IT.

        This phase's responsibility ends at handing a paid run to the queue.
        PDF rendering and bulk email are PS B8 (Phase 5), implemented
        separately and concurrently; deliberately nothing in this codebase
        path renders, formats or sends anything, and no PDF or mail dependency
        is imported.

        The task is kicked BY NAME through `AsyncKicker` rather than by
        importing a task function, precisely so this phase does not have to
        author — or guess the signature of — Phase 5's worker. The name and
        the payload below are the contract between the two phases; the payload
        carries public ids and stringified Decimals only, per Architecture §6
        ("`str()` across any JSON boundary — Taskiq payload...").

        Restricted to PAID runs: emailing a payslip is a communication to a
        person about money they have been paid, and sending it before the run
        is finalized invites a correction email chasing it.
        """
        payrun = await self.get_or_404(public_id)
        payrun = await self._reload(payrun.id)

        if payrun.status != PayrunStatus.PAID:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Payslips are sent for a paid payrun; {payrun.public_id} is "
                    f"{payrun.status.value}."
                ),
            )

        payslips = await self._payslips_of(payrun)
        if not payslips:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Payrun {payrun.public_id} has no payslips to send.",
            )

        snapshots = [payslip_response(payslip) for payslip in payslips]
        payload = {
            "payrun_id": snapshots[0].payrun.id,
            "payrun_name": snapshots[0].payrun.name,
            "period_start": str(snapshots[0].payrun.period_start),
            "period_end": str(snapshots[0].payrun.period_end),
            "payslips": [
                {
                    "payslip_id": payslip.id,
                    "employee_id": payslip.employee.id,
                    "work_email": payslip.employee.work_email,
                    # str(), never float — Architecture §6/§10. A Decimal does
                    # not survive JSON as a Decimal, and the one lossy step in
                    # a payroll pipeline is the one nobody sees.
                    "net_amount": str(payslip.net_amount),
                    "gross_amount": str(payslip.gross_amount),
                }
                for payslip in snapshots
            ],
        }

        task_id = await self._kick_delivery(payload)
        await self.audit(
            actor_email,
            "ENQUEUE_PAYSLIP_DELIVERY",
            payrun.public_id,
            after_diff={"task_id": task_id, "payslips": len(payslips)},
        )
        return {"payrun_id": payrun.public_id, "task_id": task_id, "payslip_count": len(payslips)}

    @staticmethod
    async def _kick_delivery(payload: dict) -> str:
        """Publish to the broker by task name.

        Imported lazily so importing this service does not drag in the job
        stack (and its Redis connection) for the many callers that never send
        anything.

        A broker failure is surfaced as a 503, never swallowed: "queued" is
        the entire promise this endpoint makes, and a caller told it succeeded
        when nothing was queued would wait for emails that are never coming.
        """
        # `taskiq.kicker`, not the package root: this taskiq version does not
        # re-export AsyncKicker from `taskiq/__init__.py`.
        from taskiq.kicker import AsyncKicker

        from app.jobs.broker import broker

        try:
            task = await AsyncKicker(SEND_PAYSLIPS_TASK_NAME, broker, {}).kiq(payload)
        except Exception as exc:  # pragma: no cover - depends on broker health
            logger.exception("Failed to enqueue payslip delivery")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Payslip delivery queue is unavailable; the payrun is unchanged. Try again.",
            ) from exc
        return task.task_id

    # ------------------------------------------------------------- internals

    async def _resolve_employees(self, public_ids: Sequence[str]) -> list[Employee]:
        employees: list[Employee] = []
        for public_id in public_ids:
            employee = await self.employee_repo.get_by_public_id(public_id)
            if employee is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=f"Employee '{public_id}' not found"
                )
            employees.append(employee)
        return employees

    async def _replace_selection(self, payrun: Payrun, employees: Sequence[Employee]) -> None:
        """Rewrite the payrun's selection.

        A hard delete, not a soft one: `PayrunEmployee` is a pure line-item
        child with no `deleted_at` (Architecture §4) — same as
        `SalaryStructureService._replace_links`, and issued as a Core
        statement for the same reason that one is. Iterating
        `payrun.selected_employees` instead would touch a relationship that is
        NOT loaded on a row this transaction just INSERTed (nothing SELECTed
        it, so `lazy="selectin"` never ran), and a lazy load from async code is
        a `MissingGreenlet`, not a query.
        """
        await self.session.execute(
            sa_delete(PayrunEmployee).where(PayrunEmployee.payrun_id == payrun.id)
        )
        await self.session.flush()

        for employee in employees:
            await self.selection_repo.create(
                PayrunEmployee(public_id="temp", payrun_id=payrun.id, employee_id=employee.id)
            )
        await self.session.flush()

    async def _delete_payslips(self, payrun: Payrun) -> None:
        """Remove this run's payslips before recomputing.

        A HARD delete. A soft-deleted payslip still occupies
        `uq_payslip_payrun_employee`, so recompute would collide with the
        tombstone of the payslip it is replacing. Deleted through the ORM so
        `Payslip.lines` (cascade="all, delete-orphan") goes with it and the
        identity map does not keep serving rows the database no longer has.
        """
        for payslip in await self._payslips_of(payrun):
            await self.session.delete(payslip)
        await self.session.flush()

    async def _payslips_of(self, payrun: Payrun) -> list[Payslip]:
        stmt = (
            select(Payslip)
            .where(Payslip.payrun_id == payrun.id, Payslip.deleted_at.is_(None))
            .options(*_PAYSLIP_LOADS)
            .order_by(Payslip.id)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def _load_structure(self, structure_id: int) -> SalaryStructure:
        """The payrun's structure with its ordered links and their rules.

        `rule_links` is `lazy="selectin"` and ordered by sequence on the model,
        so the resolver's precondition ("`structure.rule_links` must already
        be loaded") is met by the model itself; this spells the loads out
        anyway because `populate_existing` elsewhere in this transaction can
        expire them, and a lazy load from inside the compute loop would be a
        MissingGreenlet.
        """
        stmt = (
            select(SalaryStructure)
            .where(SalaryStructure.id == structure_id)
            .options(selectinload(SalaryStructure.rule_links))
        )
        structure = (await self.session.execute(stmt)).scalar_one_or_none()
        if structure is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This payrun's salary structure no longer exists; it cannot be computed.",
            )
        return structure

    @staticmethod
    def _aggregate_blocking(payslips: Sequence[Payslip]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for payslip in payslips:
            for code, count in blocking_summary(payslip.warnings or []).items():
                counts[code] = counts.get(code, 0) + count
        return counts

    async def _reload(self, internal_id: int) -> Payrun:
        """Re-read the payrun with everything a response renders.

        Always `populate_existing`, for two independent reasons:

          * after waiting on the advisory lock, this session's identity map
            holds a copy from BEFORE the winner committed, and a plain
            `select()` would hand that stale object back rather than the row
            the database actually has;
          * after `_replace_selection` rewrites `payrun_employees` with a Core
            statement, the ORM's cached collection still describes the old
            set. Re-populating is what makes the response show the selection
            that was just saved.
        """
        stmt = (
            select(Payrun)
            .where(Payrun.id == internal_id)
            .options(*_PAYRUN_LOADS)
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one()

    async def _flush_or_conflict(self) -> None:
        try:
            await self.session.flush()
        except StaleDataError as exc:
            await self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This payrun changed while it was being processed; refresh and try again.",
            ) from exc

    @staticmethod
    def _assert_version(payrun: Payrun, version: int) -> None:
        if payrun.version != version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Payrun {payrun.public_id} has moved on (version {payrun.version}, you sent "
                    f"{version}); refresh and try again."
                ),
            )

    @staticmethod
    def _assert_editable(payrun: Payrun) -> None:
        if payrun.status not in RECOMPUTABLE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Payrun {payrun.public_id} is {payrun.status.value} and is preserved as history "
                    "(PS B6); its name, notes and employee selection can no longer be changed."
                ),
            )

    @staticmethod
    def _assert_recomputable(payrun: Payrun) -> None:
        if payrun.status not in RECOMPUTABLE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Payrun {payrun.public_id} is {payrun.status.value}; a finalized run is never "
                    "recomputed in place (PS B6). Create a new payrun for the corrected figures."
                ),
            )


#: The Taskiq task name Phase 5's worker registers. Named here, in the phase
#: that publishes it, because the publisher and the consumer have to agree on
#: exactly one string and only one of them can own it.
SEND_PAYSLIPS_TASK_NAME = "send_payslips"


def _jsonable_report(report: dict) -> dict:
    """The validation report with its enum stringified, for an HTTPException
    detail (which FastAPI serialises without a response_model to guide it)."""
    return {**report, "status": report["status"].value}


class PayslipService(BaseService[Payslip]):
    """Payslip reads, and the one mutation payroll actually has: deleting a
    payslip from a run that has not been finalized.

    There is no "edit this payslip" method, on purpose. Every figure on a
    payslip is the output of a Salary Rule over a contract and a period; a
    wrong figure means a wrong input or a wrong rule, and both are fixed by
    correcting the source and recomputing — which is audited, reproducible,
    and leaves the rule and the payslip agreeing with each other. Typing a
    replacement number over the top of a computed one leaves a payslip no rule
    can explain, which is exactly what PS B7's rule-by-rule breakdown exists to
    prevent.
    """

    def __init__(self, session: AsyncSession):
        super().__init__(session, PayslipRepository(session), entity_name="Payslip")

    async def list_payslips(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        payrun_id: Optional[str] = None,
        employee_id: Optional[str] = None,
        tenant_id: str = "default",
    ) -> Tuple[List[Payslip], int]:
        conditions = [Payslip.tenant_id == tenant_id, Payslip.deleted_at.is_(None)]

        if payrun_id:
            payrun = await PayrunRepository(self.session).get_by_public_id(payrun_id)
            if payrun is None:
                raise HTTPException(status_code=404, detail=f"Payrun '{payrun_id}' not found")
            conditions.append(Payslip.payrun_id == payrun.id)
        if employee_id:
            employee = await EmployeeRepository(self.session).get_by_public_id(employee_id)
            if employee is None:
                raise HTTPException(status_code=404, detail=f"Employee '{employee_id}' not found")
            conditions.append(Payslip.employee_id == employee.id)

        total = (await self.session.execute(select(func.count(Payslip.id)).where(*conditions))).scalar() or 0
        stmt = (
            select(Payslip)
            .where(*conditions)
            .options(*_PAYSLIP_LOADS)
            .order_by(Payslip.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = list((await self.session.execute(stmt)).scalars().all())
        return rows, total

    async def read_payslip(self, public_id: str) -> Payslip:
        payslip = await self.get_or_404(public_id)
        stmt = select(Payslip).where(Payslip.id == payslip.id).options(*_PAYSLIP_LOADS)
        return (await self.session.execute(stmt)).scalar_one()

    async def delete_payslip(self, public_id: str, version: int, *, actor_email: str) -> Payslip:
        payslip = await self.read_payslip(public_id)
        if payslip.version != version:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Payslip {payslip.public_id} has moved on; refresh and try again.",
            )
        _assert_payslips_mutable(payslip.payrun, action="deleted")
        return await self.soft_delete(payslip, actor=actor_email, action="DELETE_PAYSLIP")


def _assert_payslips_mutable(payrun: Payrun, *, action: str) -> None:
    """The immutability wall, shared by every path that would change a
    payslip belonging to a finalized run.

    A payslip is not a document that can be edited once its run is validated
    or paid — it is the record of what somebody was paid.
    """
    if payrun.status in (PayrunStatus.VALIDATED, PayrunStatus.PAID):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Payrun {payrun.public_id} is {payrun.status.value}; its payslips are immutable and "
                f"cannot be {action}. A finalized run is preserved as history (PS B6)."
            ),
        )
