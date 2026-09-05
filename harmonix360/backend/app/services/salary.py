"""Salary configuration services — Structure and Rule (PS A5/A6).

RBAC (Architecture §5): Salary Structures and Rules are the one pair in the
matrix where read and write split across roles — HR Payroll User is
READ-ONLY, and only HR Payroll Manager (and Admin) may author. The router
uses PAYROLL_ROLES for reads and PAYROLL_ADMIN_ROLES for every mutation.

Two things this module is responsible for that the resolver
(`app/services/salary_resolver.py`) is not:
  * translating the `salary_rules.code` / `salary_structures.code` unique
    violations into a 409 naming the field, the same way
    `app/services/employee.py` does for `work_email`;
  * calling `validate_structure_rule_order` at structure-save time — the
    resolver only VALIDATES an already-decided ordering; deciding what that
    ordering is (from the incoming `rules` list) and refusing to save when it
    doesn't validate is this module's job.
"""
from typing import Iterable, List, Optional, Sequence

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contract import Contract
from app.models.salary import SalaryRule, SalaryStructure, SalaryStructureRule
from app.repositories.hr import SalaryRuleRepository, SalaryStructureRepository, SalaryStructureRuleRepository
from app.schemas.salary import SalaryStructureRuleInput
from app.services.base import BaseService
from app.services.salary_resolver import StructureValidationError, validate_structure_rule_order

UNIQUE_VIOLATION_SQLSTATE = "23505"


class SalaryRuleService(BaseService[SalaryRule]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, SalaryRuleRepository(session), entity_name="SalaryRule")

    async def create_rule(self, dto, *, actor_email: str) -> SalaryRule:
        rule = SalaryRule(
            public_id="temp",
            name=dto.name,
            code=dto.code,
            category=dto.category,
            sequence=dto.sequence,
            computation_method=dto.computation_method,
            amount=dto.amount,
            percentage_base_code=dto.percentage_base_code,
            expression=dto.expression,
            is_active=dto.is_active,
            description=dto.description,
        )
        try:
            return await self.create(
                rule,
                actor=actor_email,
                action="CREATE_SALARY_RULE",
                after_diff={"code": dto.code, "computation_method": dto.computation_method.value},
            )
        except IntegrityError as exc:
            await self.session.rollback()
            self._translate_unique_violation(exc, dto.code)
            raise

    async def update_rule(self, public_id: str, dto, *, actor_email: str) -> SalaryRule:
        rule = await self.get_or_404(public_id)
        before = {"code": rule.code, "computation_method": rule.computation_method.value}

        rule.name = dto.name
        rule.code = dto.code
        rule.category = dto.category
        rule.sequence = dto.sequence
        rule.computation_method = dto.computation_method
        rule.amount = dto.amount
        rule.percentage_base_code = dto.percentage_base_code
        rule.expression = dto.expression
        rule.is_active = dto.is_active
        rule.description = dto.description

        try:
            return await self.update(
                rule,
                actor=actor_email,
                action="UPDATE_SALARY_RULE",
                before_diff=before,
                after_diff={"code": dto.code, "computation_method": dto.computation_method.value},
            )
        except IntegrityError as exc:
            await self.session.rollback()
            self._translate_unique_violation(exc, dto.code)
            raise

    async def delete_rule(self, public_id: str, *, actor_email: str) -> SalaryRule:
        rule = await self.get_or_404(public_id)
        return await self.soft_delete(rule, actor=actor_email, action="DELETE_SALARY_RULE")

    def _translate_unique_violation(self, exc: IntegrityError, code: str) -> None:
        if getattr(exc.orig, "sqlstate", None) != UNIQUE_VIOLATION_SQLSTATE:
            return
        if "uq_salary_rule_code_tenant" in str(exc.orig):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A salary rule with code '{code}' already exists",
            ) from exc


class SalaryStructureService(BaseService[SalaryStructure]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, SalaryStructureRepository(session), entity_name="SalaryStructure")
        self.rule_repo = SalaryRuleRepository(session)
        self.link_repo = SalaryStructureRuleRepository(session)

    async def list_with_rules(
        self, limit: int = 50, offset: int = 0
    ) -> tuple[List[SalaryStructure], int]:
        # `rule_links` is a selectin relationship, so this is one extra query,
        # not N.
        return await self.repo.list_active(limit=limit, offset=offset)

    async def contract_usage_counts(self, structures: Iterable[SalaryStructure]) -> dict[int, int]:
        """How many (non-deleted) Contracts point at each structure.

        Computed for the whole page in one grouped query rather than once per
        row, same reasoning as `ContractService.active_contract_ids`: a
        per-row COUNT subquery would be N round trips for an N-row page.
        """
        ids = [s.id for s in structures]
        if not ids:
            return {}
        stmt = (
            select(Contract.salary_structure_id, func.count(Contract.id))
            .where(Contract.salary_structure_id.in_(ids), Contract.deleted_at.is_(None))
            .group_by(Contract.salary_structure_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return {structure_id: count for structure_id, count in rows}

    async def create_structure(self, dto, *, actor_email: str) -> SalaryStructure:
        structure = SalaryStructure(
            public_id="temp",
            name=dto.name,
            code=dto.code,
            is_active=dto.is_active,
            description=dto.description,
        )

        ordered_rules = await self._resolve_and_order_rules(dto.rules)
        self._validate_order_or_400(ordered_rules)

        try:
            created = await self.create(
                structure,
                actor=actor_email,
                action="CREATE_SALARY_STRUCTURE",
                after_diff={"code": dto.code, "rule_count": len(dto.rules)},
            )
        except IntegrityError as exc:
            await self.session.rollback()
            self._translate_unique_violation(exc, dto.code)
            raise

        await self._replace_links(created, dto.rules, ordered_rules)
        return await self._reload(created.id)

    async def update_structure(self, public_id: str, dto, *, actor_email: str) -> SalaryStructure:
        structure = await self.get_or_404(public_id)
        before = {"code": structure.code, "is_active": structure.is_active}

        provided = dto.model_fields_set
        if "name" in provided:
            structure.name = dto.name
        if "code" in provided:
            structure.code = dto.code
        if "is_active" in provided:
            structure.is_active = dto.is_active
        if "description" in provided:
            structure.description = dto.description

        ordered_rules: Optional[List[SalaryRule]] = None
        if dto.rules is not None:
            ordered_rules = await self._resolve_and_order_rules(dto.rules)
            self._validate_order_or_400(ordered_rules)

        try:
            updated = await self.update(
                structure,
                actor=actor_email,
                action="UPDATE_SALARY_STRUCTURE",
                before_diff=before,
                after_diff={"code": structure.code, "is_active": structure.is_active},
            )
        except IntegrityError as exc:
            await self.session.rollback()
            self._translate_unique_violation(exc, structure.code)
            raise

        if dto.rules is not None:
            await self._replace_links(updated, dto.rules, ordered_rules)

        return await self._reload(updated.id)

    async def delete_structure(self, public_id: str, *, actor_email: str) -> SalaryStructure:
        structure = await self.get_or_404(public_id)
        return await self.soft_delete(structure, actor=actor_email, action="DELETE_SALARY_STRUCTURE")

    # ------------------------------------------------------------- internals

    async def _resolve_and_order_rules(
        self, rule_inputs: Sequence[SalaryStructureRuleInput]
    ) -> List[SalaryRule]:
        """Decode each `salary_rule_id` public id and sort by the requested
        `sequence`, breaking ties by public id for determinism. This ordering
        is what `validate_structure_rule_order` checks and what gets written
        to `SalaryStructureRule.sequence`."""
        resolved: List[tuple[int, SalaryRule]] = []
        for entry in rule_inputs:
            rule = await self.rule_repo.get_by_public_id(entry.salary_rule_id)
            if rule is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"SalaryRule '{entry.salary_rule_id}' not found",
                )
            resolved.append((entry.sequence, rule))
        resolved.sort(key=lambda pair: (pair[0], pair[1].code))
        return [rule for _, rule in resolved]

    def _validate_order_or_400(self, ordered_rules: Sequence[SalaryRule]) -> None:
        try:
            validate_structure_rule_order(ordered_rules)
        except StructureValidationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    async def _replace_links(
        self,
        structure: SalaryStructure,
        rule_inputs: Sequence[SalaryStructureRuleInput],
        ordered_rules: Sequence[SalaryRule],
    ) -> None:
        """Delete the structure's links and write the new set.

        A hard DELETE, not a soft one: `SalaryStructureRule` has no
        `deleted_at` (a line-item child, Architecture §4) — same reasoning as
        `WorkingScheduleService._replace_lines`.
        """
        await self.session.execute(
            delete(SalaryStructureRule).where(SalaryStructureRule.structure_id == structure.id)
        )

        rule_by_public_id = {rule.public_id: rule for rule in ordered_rules}
        for entry in rule_inputs:
            rule = rule_by_public_id[entry.salary_rule_id]
            await self.link_repo.create(
                SalaryStructureRule(
                    public_id="temp",
                    structure_id=structure.id,
                    salary_rule_id=rule.id,
                    sequence=entry.sequence,
                )
            )
        await self.session.flush()

    async def _reload(self, internal_id: int) -> SalaryStructure:
        """Re-read with `rule_links` freshly populated — same
        `populate_existing` reasoning as `WorkingScheduleService._reload`:
        the in-session instance's `rule_links` collection was loaded before
        `_replace_links` ran and would otherwise come back stale."""
        stmt = (
            select(SalaryStructure)
            .where(SalaryStructure.id == internal_id)
            .execution_options(populate_existing=True)
        )
        structure = (await self.session.execute(stmt)).scalar_one()
        await self.session.refresh(structure, attribute_names=["rule_links"])
        return structure

    def _translate_unique_violation(self, exc: IntegrityError, code: str) -> None:
        if getattr(exc.orig, "sqlstate", None) != UNIQUE_VIOLATION_SQLSTATE:
            return
        if "uq_salary_structure_code_tenant" in str(exc.orig):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A salary structure with code '{code}' already exists",
            ) from exc
