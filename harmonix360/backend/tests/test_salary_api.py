"""SalaryStructure / SalaryRule (PS A5/A6).

This is the phase whose requirement matters most: hand-compute a realistic
payslip structure on paper and assert `resolve_salary_structure` reproduces
it exactly — see `test_hand_computed_structure_matches_resolver_exactly`
below. Everything else (CRUD, RBAC, the save-time formula-ordering guard) is
tested the same way the rest of this codebase tests its entities.
"""
from decimal import Decimal

import pytest
import simpleeval

from app.models.enums import SalaryRuleCategory, SalaryRuleComputation, UserRole
from app.models.salary import SalaryRule
from app.services.salary_resolver import (
    SEED_CONTEXT_NAMES,
    FormulaEvaluationError,
    ResolvedRule,
    StructureValidationError,
    resolve_ordered_rules,
    validate_formula_syntax,
    validate_structure_rule_order,
)
from tests.conftest import auth_headers, unique_email

HR_MANAGER = auth_headers(UserRole.HR_MANAGER)
EMPLOYEE = auth_headers(UserRole.EMPLOYEE)
PAYROLL_USER = auth_headers(UserRole.HR_PAYROLL_USER)
PAYROLL_MANAGER = auth_headers(UserRole.HR_PAYROLL_MANAGER)
ADMIN = auth_headers(UserRole.ADMIN)


def _rule(code: str, method: SalaryRuleComputation, **kwargs) -> SalaryRule:
    """Builds a real `SalaryRule` ORM instance, never flushed — the resolver
    takes plain objects, so no session/DB is needed to test it."""
    return SalaryRule(
        public_id=f"srule_{code.lower()}",
        name=code.title(),
        code=code,
        category=kwargs.pop("category", SalaryRuleCategory.ALLOWANCE),
        sequence=kwargs.pop("sequence", 100),
        computation_method=method,
        is_active=True,
        **kwargs,
    )


# ===========================================================================
# The resolver — pure unit tests, no DB (app/services/salary_resolver.py)
# ===========================================================================

# --------------------------------------------------------------- (a) fixed

def test_a_fixed_rule_computes_its_amount():
    basic = _rule("BASIC", SalaryRuleComputation.FIXED, category=SalaryRuleCategory.BASIC, amount=Decimal("30000.00"))
    resolved = resolve_ordered_rules([basic], {})
    assert resolved == [ResolvedRule(code="BASIC", category=SalaryRuleCategory.BASIC, amount=Decimal("30000.00"))]


# ---------------------------------------------- (b) percentage of an earlier rule

def test_b_percentage_rule_references_an_earlier_rule():
    basic = _rule("BASIC", SalaryRuleComputation.FIXED, category=SalaryRuleCategory.BASIC, amount=Decimal("30000.00"))
    hra = _rule(
        "HRA",
        SalaryRuleComputation.PERCENTAGE,
        category=SalaryRuleCategory.ALLOWANCE,
        amount=Decimal("40.00"),
        percentage_base_code="BASIC",
    )
    resolved = resolve_ordered_rules([basic, hra], {})
    amounts = {r.code: r.amount for r in resolved}
    assert amounts["BASIC"] == Decimal("30000.00")
    assert amounts["HRA"] == Decimal("12000.00")  # 40% of 30000


# ------------------------------- (c) formula referencing two rules + a seed input

def test_c_formula_references_two_earlier_rules_and_a_seed_input():
    basic = _rule("BASIC", SalaryRuleComputation.FIXED, category=SalaryRuleCategory.BASIC, amount=Decimal("30000.00"))
    hra = _rule(
        "HRA", SalaryRuleComputation.PERCENTAGE, amount=Decimal("40.00"), percentage_base_code="BASIC"
    )
    # WORKED_DAYS is a seed input (Architecture §7), not a rule — proves a
    # formula can mix seed context and earlier rule codes in one expression.
    bonus = _rule(
        "BONUS",
        SalaryRuleComputation.FORMULA,
        expression="(BASIC + HRA) * WORKED_DAYS / 30",
    )
    resolved = resolve_ordered_rules([basic, hra, bonus], {"WORKED_DAYS": Decimal("30")})
    amounts = {r.code: r.amount for r in resolved}
    assert amounts["BONUS"] == Decimal("42000.00")


# ------------------------------------- (d) a formula referencing a LATER rule

def test_d_formula_referencing_a_later_rule_fails_at_structure_save_time():
    """PT is deducted from GROSS, but is listed BEFORE gross runs — this must
    be refused when the structure is saved, never merely at payrun time."""
    net = _rule("NET", SalaryRuleComputation.FORMULA, expression="GROSS - PT")
    gross = _rule("GROSS", SalaryRuleComputation.FIXED, amount=Decimal("100.00"))

    with pytest.raises(StructureValidationError) as exc_info:
        validate_structure_rule_order([net, gross])
    assert exc_info.value.rule_code == "NET"
    assert "GROSS" in str(exc_info.value)


def test_d_unknown_name_in_a_formula_also_fails_at_save_time():
    typo = _rule("NET", SalaryRuleComputation.FORMULA, expression="GROSSS - PT")  # typo'd name
    with pytest.raises(StructureValidationError) as exc_info:
        validate_structure_rule_order([typo])
    assert "GROSSS" in str(exc_info.value)


def test_d_percentage_rule_with_an_unknown_base_fails_at_save_time():
    hra = _rule("HRA", SalaryRuleComputation.PERCENTAGE, amount=Decimal("40.00"), percentage_base_code="BASIC")
    with pytest.raises(StructureValidationError) as exc_info:
        validate_structure_rule_order([hra])  # BASIC never runs before HRA here
    assert exc_info.value.rule_code == "HRA"


def test_d_a_rule_may_not_reference_its_own_code():
    """`known` only gains a rule's own code AFTER it validates — a
    self-reference is indistinguishable from, and rejected the same way as,
    a forward reference."""
    circular = _rule("X", SalaryRuleComputation.FORMULA, expression="X + 1")
    with pytest.raises(StructureValidationError):
        validate_structure_rule_order([circular])


def test_d_seed_context_names_are_available_from_the_start():
    """The one case that must NOT raise: every seed name is available to the
    very first rule in the structure."""
    first = _rule("BASIC", SalaryRuleComputation.FORMULA, expression="CONTRACT_WAGE - UNPAID_LEAVE_DAYS")
    assert SEED_CONTEXT_NAMES == {"WORKED_DAYS", "CONTRACT_WAGE", "UNPAID_LEAVE_DAYS"}
    validate_structure_rule_order([first])  # must not raise


# --------------------------------- (e) formula-injection rejected by simpleeval

def test_e_formula_injection_attempt_is_rejected_by_simpleevals_restricted_grammar():
    """Bypasses this codebase's own AST whitelist entirely (the rule is built
    directly as an ORM object, not through the Pydantic schema) so this
    exercises simpleeval's OWN restricted grammar: `functions={}` means an
    injection attempt fails as an undefined function, not because our static
    check happened to catch it first."""
    evil = _rule("EVIL", SalaryRuleComputation.FORMULA, expression="__import__('os').system('echo pwned')")

    with pytest.raises(FormulaEvaluationError) as exc_info:
        resolve_ordered_rules([evil], {})
    assert "not defined" in str(exc_info.value).lower() or "rejected" in str(exc_info.value).lower()


def test_e_simpleeval_itself_refuses_to_call_an_undefined_function():
    """The same attempt, one layer down: simpleeval.SimpleEval directly, with
    no wrapper of ours in the way at all."""
    evaluator = simpleeval.SimpleEval(functions={}, names={})
    with pytest.raises(simpleeval.InvalidExpression):
        evaluator.eval("__import__('os')")


def test_e_our_own_static_check_also_refuses_the_call_syntax_outright():
    """Defense in depth, the other direction: our AST whitelist (used at
    rule-save time, before simpleeval ever runs) rejects a Call node on
    sight, regardless of whether the called name is known."""
    with pytest.raises(ValueError):
        validate_formula_syntax("__import__('os').system('echo pwned')")


# ===========================================================================
# THE test: a hand-computed, realistic structure, matched exactly
# ===========================================================================

def test_hand_computed_structure_matches_resolver_exactly():
    """Basic fixed at 30000.00; HRA = 40% of Basic; Gross = Basic + HRA; PT a
    fixed 200.00 deduction; Net = Gross - PT.

    Hand computation:
        BASIC  = 30000.00
        HRA    = 30000.00 * 40% = 12000.00
        GROSS  = 30000.00 + 12000.00 = 42000.00
        PT     = 200.00
        NET    = 42000.00 - 200.00 = 41800.00

    This is the exact shape Phase 4 will run for real: a basic, a
    percentage allowance, a formula gross, a fixed deduction, and a formula
    net — every computation method this phase implements, composed the way
    an actual payslip composes them.
    """
    basic = _rule("BASIC", SalaryRuleComputation.FIXED, category=SalaryRuleCategory.BASIC, sequence=10, amount=Decimal("30000.00"))
    hra = _rule(
        "HRA", SalaryRuleComputation.PERCENTAGE, category=SalaryRuleCategory.ALLOWANCE, sequence=20,
        amount=Decimal("40.00"), percentage_base_code="BASIC",
    )
    gross = _rule("GROSS", SalaryRuleComputation.FORMULA, category=SalaryRuleCategory.GROSS, sequence=30, expression="BASIC + HRA")
    pt = _rule("PT", SalaryRuleComputation.FIXED, category=SalaryRuleCategory.DEDUCTION, sequence=40, amount=Decimal("200.00"))
    net = _rule("NET", SalaryRuleComputation.FORMULA, category=SalaryRuleCategory.NET, sequence=50, expression="GROSS - PT")

    ordered = [basic, hra, gross, pt, net]

    # Save-time validation must accept this well-formed, correctly ordered
    # structure without raising.
    validate_structure_rule_order(ordered)

    resolved = resolve_ordered_rules(ordered, seed_context={})
    amounts = {r.code: r.amount for r in resolved}

    assert amounts["BASIC"] == Decimal("30000.00")
    assert amounts["HRA"] == Decimal("12000.00")
    assert amounts["GROSS"] == Decimal("42000.00")
    assert amounts["PT"] == Decimal("200.00")
    assert amounts["NET"] == Decimal("41800.00")

    # Order and category are preserved end to end — this is what becomes a
    # PayslipLine per row in Phase 4.
    assert [r.code for r in resolved] == ["BASIC", "HRA", "GROSS", "PT", "NET"]
    assert [r.category for r in resolved] == [
        SalaryRuleCategory.BASIC,
        SalaryRuleCategory.ALLOWANCE,
        SalaryRuleCategory.GROSS,
        SalaryRuleCategory.DEDUCTION,
        SalaryRuleCategory.NET,
    ]
    for r in resolved:
        assert isinstance(r.amount, Decimal)  # Architecture §10 — never float


# ===========================================================================
# API — CRUD (PS A6/A5)
# ===========================================================================

def _rule_body(code: str, **overrides) -> dict:
    body = {
        "name": code.title(),
        "code": code,
        "category": "allowance",
        "sequence": 100,
        "computation_method": "fixed",
        "amount": "100.00",
    }
    body.update(overrides)
    return body


async def _create_rule(client, code: str, headers=PAYROLL_MANAGER, **overrides) -> dict:
    resp = await client.post("/api/v1/salary-rules/", json=_rule_body(code, **overrides), headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_salary_rule_crud_round_trip(client, cleanup_salary_config):
    code = f"BASIC_{unique_email('x').split('@')[0][-8:].upper()}"
    created = await _create_rule(client, code, category="basic", amount="30000.00")
    public_id = created["id"]
    assert public_id.startswith("srule_")
    assert created["computation_method"] == "fixed"

    fetched = await client.get(f"/api/v1/salary-rules/{public_id}", headers=PAYROLL_MANAGER)
    assert fetched.status_code == 200
    assert fetched.json()["code"] == code

    updated = await client.patch(
        f"/api/v1/salary-rules/{public_id}",
        json={**_rule_body(code, category="basic", amount="35000.00"), "version": created["version"]},
        headers=PAYROLL_MANAGER,
    )
    assert updated.status_code == 200, updated.text
    assert Decimal(updated.json()["amount"]) == Decimal("35000.00")

    deleted = await client.delete(f"/api/v1/salary-rules/{public_id}", headers=PAYROLL_MANAGER)
    assert deleted.status_code == 200
    assert (await client.get(f"/api/v1/salary-rules/{public_id}", headers=PAYROLL_MANAGER)).status_code == 404


async def test_duplicate_salary_rule_code_is_a_409(client, cleanup_salary_config):
    code = f"DUP_{unique_email('x').split('@')[0][-8:].upper()}"
    await _create_rule(client, code)
    resp = await client.post("/api/v1/salary-rules/", json=_rule_body(code), headers=PAYROLL_MANAGER)
    assert resp.status_code == 409, resp.text


@pytest.mark.parametrize(
    "body,expected_error_fragment",
    [
        ({"computation_method": "fixed", "amount": None}, "requires 'amount'"),
        ({"computation_method": "fixed", "amount": "10.00", "expression": "1+1"}, "must not set"),
        ({"computation_method": "percentage", "amount": "10.00", "percentage_base_code": None}, "requires both"),
        ({"computation_method": "formula", "amount": None, "expression": None}, "requires 'expression'"),
        ({"computation_method": "formula", "amount": None, "expression": "os.system('x')"}, "unsupported syntax"),
        ({"computation_method": "formula", "amount": None, "expression": "2 +"}, "not a valid expression"),
    ],
)
async def test_rule_computation_fields_are_validated_per_method(client, body, expected_error_fragment):
    payload = _rule_body("WHATEVER", **body)
    resp = await client.post("/api/v1/salary-rules/", json=payload, headers=PAYROLL_MANAGER)
    assert resp.status_code == 422, resp.text
    assert expected_error_fragment in resp.text


async def test_salary_structure_crud_round_trip_with_rule_count_and_usage(client, cleanup_salary_config, cleanup_employees):
    suffix = unique_email("x").split("@")[0][-8:].upper()
    basic = await _create_rule(client, f"BASIC_{suffix}", category="basic", amount="20000.00")
    hra = await _create_rule(
        client, f"HRA_{suffix}", category="allowance", computation_method="percentage",
        amount="40.00", percentage_base_code=f"BASIC_{suffix}",
    )

    created = await client.post(
        "/api/v1/salary-structures/",
        json={
            "name": "Standard Structure",
            "code": f"STD_{suffix}",
            "rules": [
                {"salary_rule_id": basic["id"], "sequence": 10},
                {"salary_rule_id": hra["id"], "sequence": 20},
            ],
        },
        headers=PAYROLL_MANAGER,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["id"].startswith("sstr_")
    assert body["rule_count"] == 2
    assert body["contract_usage_count"] == 0
    assert [r["salary_rule"]["code"] for r in body["rules"]] == [f"BASIC_{suffix}", f"HRA_{suffix}"]

    # Contract-usage count: create a Contract against this structure and see
    # the count move.
    employee = await client.post(
        "/api/v1/employees/",
        json={"first_name": "Payroll", "last_name": "Subject", "work_email": unique_email("payroll")},
        headers=HR_MANAGER,
    )
    assert employee.status_code == 201, employee.text
    contract = await client.post(
        "/api/v1/contracts/",
        json={
            "employee_id": employee.json()["id"],
            "wage": "20000.00",
            "start_date": "2026-01-01",
            "salary_structure_id": body["id"],
        },
        headers=HR_MANAGER,
    )
    assert contract.status_code == 201, contract.text

    refetched = await client.get(f"/api/v1/salary-structures/{body['id']}", headers=PAYROLL_MANAGER)
    assert refetched.json()["contract_usage_count"] == 1

    # PATCH replaces the rule set wholesale.
    patched = await client.patch(
        f"/api/v1/salary-structures/{body['id']}",
        json={"rules": [{"salary_rule_id": basic["id"], "sequence": 10}]},
        headers=PAYROLL_MANAGER,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["rule_count"] == 1

    deleted = await client.delete(f"/api/v1/salary-structures/{body['id']}", headers=PAYROLL_MANAGER)
    assert deleted.status_code == 200


async def test_structure_save_rejects_a_formula_referencing_a_later_rule(client, cleanup_salary_config):
    suffix = unique_email("x").split("@")[0][-8:].upper()
    gross = await _create_rule(client, f"GROSS_{suffix}", category="gross", amount="100.00")
    net = await _create_rule(
        client, f"NET_{suffix}", category="net", computation_method="formula",
        amount=None, expression=f"GROSS_{suffix} - 1",
    )

    resp = await client.post(
        "/api/v1/salary-structures/",
        json={
            "name": "Bad order",
            "code": f"BAD_{suffix}",
            # NET listed (and thus sequenced) before GROSS — must be refused.
            "rules": [
                {"salary_rule_id": net["id"], "sequence": 10},
                {"salary_rule_id": gross["id"], "sequence": 20},
            ],
        },
        headers=PAYROLL_MANAGER,
    )
    assert resp.status_code == 400, resp.text
    assert f"GROSS_{suffix}" in resp.text


async def test_structure_create_404s_on_an_unknown_rule_reference(client, cleanup_salary_config):
    resp = await client.post(
        "/api/v1/salary-structures/",
        json={"name": "X", "code": f"X_{unique_email('x').split('@')[0][-8:].upper()}", "rules": [{"salary_rule_id": "srule_doesnotexist"}]},
        headers=PAYROLL_MANAGER,
    )
    assert resp.status_code == 404, resp.text


# ===========================================================================
# RBAC (Architecture §5) — the one place read/write split across roles
# ===========================================================================

async def test_hr_manager_and_employee_are_denied_every_salary_operation(client, cleanup_salary_config):
    assert (await client.get("/api/v1/salary-rules/", headers=HR_MANAGER)).status_code == 403
    assert (await client.get("/api/v1/salary-rules/", headers=EMPLOYEE)).status_code == 403
    assert (await client.get("/api/v1/salary-structures/", headers=HR_MANAGER)).status_code == 403
    assert (await client.get("/api/v1/salary-structures/", headers=EMPLOYEE)).status_code == 403
    assert (
        await client.post("/api/v1/salary-rules/", json=_rule_body("X"), headers=HR_MANAGER)
    ).status_code == 403


async def test_hr_payroll_user_is_read_only_on_salary_config(client, cleanup_salary_config):
    # Read: allowed.
    assert (await client.get("/api/v1/salary-rules/", headers=PAYROLL_USER)).status_code == 200
    assert (await client.get("/api/v1/salary-structures/", headers=PAYROLL_USER)).status_code == 200

    # Write: denied, on every mutating verb.
    assert (
        await client.post("/api/v1/salary-rules/", json=_rule_body("Y"), headers=PAYROLL_USER)
    ).status_code == 403
    assert (
        await client.post(
            "/api/v1/salary-structures/", json={"name": "Y", "code": "Y", "rules": []}, headers=PAYROLL_USER
        )
    ).status_code == 403

    # A rule created by a manager cannot be edited or deleted by the read-only role.
    rule = await _create_rule(client, f"RO_{unique_email('x').split('@')[0][-8:].upper()}")
    assert (
        await client.patch(
            f"/api/v1/salary-rules/{rule['id']}",
            json={**_rule_body(rule["code"], amount="1.00"), "version": rule["version"]},
            headers=PAYROLL_USER,
        )
    ).status_code == 403
    assert (await client.delete(f"/api/v1/salary-rules/{rule['id']}", headers=PAYROLL_USER)).status_code == 403


@pytest.mark.parametrize("headers", [PAYROLL_MANAGER, ADMIN])
async def test_hr_payroll_manager_and_admin_have_full_crud(client, headers, cleanup_salary_config):
    rule = await _create_rule(client, f"FULL_{unique_email('x').split('@')[0][-8:].upper()}", headers=headers)
    assert (
        await client.patch(
            f"/api/v1/salary-rules/{rule['id']}",
            json={**_rule_body(rule["code"], amount="2.00"), "version": rule["version"]},
            headers=headers,
        )
    ).status_code == 200
    assert (await client.delete(f"/api/v1/salary-rules/{rule['id']}", headers=headers)).status_code == 200
