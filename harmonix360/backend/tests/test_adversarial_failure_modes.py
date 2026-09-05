"""Adversarial / failure-mode regression suite (Phases 1-4, 6, Gap-fix).

TESTING ONLY. Nothing in this file changes feature behaviour, and no
production module is imported for anything but reading. If a case here fails,
the finding is reported to the team — it is not patched from inside this file.

WHAT EVERY TEST HERE ASSERTS
----------------------------
The other suites ask "does the happy path produce the right number?". This one
asks a narrower and nastier question of each failure path:

    when this is attacked, does the system produce a CONTROLLED, EXPLAINABLE
    result — not a crash, and not silent corruption?

So each test asserts three things, and the third is the one that distinguishes
this suite from the coverage that already exists:

  1. **Controlled** — a specific, deliberate status code. Never 500, never a
     bare exception escaping to the client.
  2. **Explainable** — the response says what went wrong in words a person
     acting on it can use. An empty or generic body fails the test even when
     the status code is right.
  3. **Uncorrupted** — the database is inspected DIRECTLY afterwards, past the
     API's own filters, to prove the rejected operation left nothing behind. A
     rejection that still wrote a row is the failure mode that would otherwise
     look like a pass.

Several of these paths already have positive coverage elsewhere
(`test_payroll_api.py`, `test_attendance_time_off.py`, `test_salary_api.py`,
`test_payroll_gaps.py`). That is deliberate: this suite is the place a
reviewer can read all eight failure modes in one file and see the same three
assertions applied to each, rather than inferring them from eight different
happy-path files.
"""

import asyncio
import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.v1.deps import CurrentUser
from app.core.database import AsyncSessionLocal
from app.core.security import decode_public_id
from app.models.contract import Contract
from app.models.enums import (
    PayslipStatus,
    SalaryRuleCategory,
    SalaryRuleComputation,
    UserRole,
)
from app.models.payroll import Payslip, PayslipLine
from app.models.time_off import TimeOffAllocation
from app.schemas.time_off import Decision
from app.services.time_off import TimeOffRequestService
from tests.test_attendance_time_off import allocation, decide
from tests.test_attendance_time_off import request as make_leave_request
from tests.test_attendance_time_off import leave_setup as _leave_setup_fixture
from tests.test_payroll_api import (
    HR_MANAGER,
    PAYROLL_MANAGER,
    PAYROLL_USER,
    attach_working_schedule,
    compute,
    idem,
    make_contract,
    make_employee,
    make_payrun,
    make_rule,
    make_structure,
    standard_structure,
)

#: Phase 2's leave fixture, re-exported under the name pytest resolves so the
#: allocation-race and insufficient-balance cases reuse the same setup the
#: Phase 2 suite validates against, rather than a second copy of it that could
#: drift. Aliased on import so the module-level name is not a redefinition.
leave_setup = _leave_setup_fixture

BASE = "/api/v1"


# --------------------------------------------------------------------------
# Shared assertions. Written once so every scenario is held to the same bar,
# and so "controlled and explainable" is a thing the code checks rather than a
# phrase in a docstring.
# --------------------------------------------------------------------------


def assert_controlled(response, expected_status: int, *, context: str):
    """A deliberate status code, never a server error."""
    assert response.status_code != 500, (
        f"{context}: the server raised rather than refusing. A failure mode that "
        f"500s is uncontrolled by definition. Body: {response.text[:400]}"
    )
    assert response.status_code == expected_status, (
        f"{context}: expected {expected_status}, got {response.status_code}. "
        f"Body: {response.text[:400]}"
    )


def assert_explainable(response, *, must_mention: tuple[str, ...], context: str):
    """The body says what went wrong, in terms the reader can act on."""
    body = response.text.lower()
    assert len(body.strip()) > 2, f"{context}: refused with an empty body"
    missing = [term for term in must_mention if term.lower() not in body]
    assert not missing, (
        f"{context}: the explanation does not mention {missing}. A refusal the "
        f"user cannot act on is not explainable. Body: {response.text[:400]}"
    )


async def count_rows(model, **filters) -> int:
    async with AsyncSessionLocal() as session:
        stmt = select(func.count(model.id))
        for column, value in filters.items():
            stmt = stmt.where(getattr(model, column) == value)
        return (await session.execute(stmt)).scalar_one()


def internal_id(public_id: str, prefix: str) -> int:
    resolved = decode_public_id(public_id, prefix)
    assert resolved is not None, f"{public_id} did not decode under prefix {prefix!r}"
    return resolved


async def clean_payrun(client, *, structure=None):
    """An employee who can actually be paid: bank details, a Mon-Fri schedule
    (LOP's denominator), an active contract, and a payrun over the period."""
    employee = await make_employee(client)
    await attach_working_schedule(client, employee["id"])
    await make_contract(client, employee["id"], wage="30000.00")
    structure = structure or await standard_structure(client)
    payrun = await make_payrun(client, structure["id"], [employee["id"]])
    return employee, structure, payrun


async def payrun_now(client, payrun):
    response = await client.get(f"{BASE}/payruns/{payrun['id']}", headers=PAYROLL_USER)
    assert response.status_code == 200, response.text
    return response.json()


# ==========================================================================
# 1. Duplicate payrun compute — recompute REPLACES, never duplicates
# ==========================================================================


async def test_1_duplicate_compute_replaces_payslips_and_never_duplicates(
    client, cleanup_employees, cleanup_schedules, cleanup_salary_config, cleanup_payroll
):
    """Compute the same payrun three times and attack it from two directions.

    The corruption this guards against is a payrun accumulating a second set of
    payslips — every row individually valid, the run's total silently doubled.
    `uq_payslip_payrun_employee` is the backstop; the recompute path deleting
    before it writes is the actual mechanism.

    Attacked two ways: a stale-version replay (the double-clicked form) must be
    REFUSED, and a legitimate re-compute must REPLACE. Both are checked against
    the payslip and payslip-line tables directly, because a duplicate set is
    exactly the thing a `.items[0]` assertion would not notice.
    """
    employee, _, payrun = await clean_payrun(client)
    payrun_pk = internal_id(payrun["id"], "prun")

    await compute(client, payrun)
    first_slips = await count_rows(Payslip, payrun_id=payrun_pk)
    first_slip_id = (
        await client.get(f"{BASE}/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()["items"][0]["id"]
    first_lines = await count_rows(
        PayslipLine, payslip_id=internal_id(first_slip_id, "pslip")
    )
    assert first_slips == 1

    # (a) Replay with the SAME version the first compute consumed.
    stale = await client.post(
        f"{BASE}/payruns/{payrun['id']}/compute",
        json={"version": payrun["version"]},
        headers={**PAYROLL_USER, **idem()},
    )
    assert_controlled(stale, 409, context="stale-version recompute")
    assert_explainable(stale, must_mention=("version",), context="stale-version recompute")
    assert await count_rows(Payslip, payrun_id=payrun_pk) == 1, (
        "a REFUSED recompute wrote a payslip anyway"
    )

    # (b) Two legitimate recomputes, each with the current version.
    for _ in range(2):
        await compute(client, await payrun_now(client, payrun))

    final_slips = await count_rows(Payslip, payrun_id=payrun_pk)
    assert final_slips == 1, (
        f"after three computes the run holds {final_slips} payslips; recompute must "
        "REPLACE the set, not append to it"
    )

    slips = (
        await client.get(f"{BASE}/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()["items"]
    assert len(slips) == 1
    final_lines = await count_rows(
        PayslipLine, payslip_id=internal_id(slips[0]["id"], "pslip")
    )
    assert final_lines == first_lines, (
        "line count drifted across recomputes — replaced lines are not accumulating "
        "correctly"
    )
    # Orphans: every remaining line belongs to a payslip that still exists.
    async with AsyncSessionLocal() as session:
        orphans = (
            await session.execute(
                select(func.count(PayslipLine.id)).where(
                    PayslipLine.payslip_id.not_in(select(Payslip.id))
                )
            )
        ).scalar_one()
    assert orphans == 0, f"{orphans} payslip line(s) survived their payslip's deletion"


# ==========================================================================
# 2. Concurrent approval race on ONE allocation
# ==========================================================================


async def test_2_concurrent_approval_race_debits_one_allocation_once(
    client, leave_setup, monkeypatch
):
    """Two approvals against one allocation with exactly enough balance for one.

    Both are held at a barrier AFTER each has read the same allocation version,
    so this is a genuine lost-update race rather than two sequential calls that
    happen to be written next to each other.

    The corruption guarded against is a double debit: `taken` exceeding
    `allocated`, or two approved requests drawing on one day of balance. The
    database's `ck_allocation_balance` CHECK is the last line, but the intended
    mechanism is the ORM's `version_id_col` on the allocation, and the loser
    must be told to refresh rather than crash.
    """
    employee, _, leave_type, _ = leave_setup
    alloc = await allocation(client, employee, leave_type, "1")
    requests = [await make_leave_request(client, employee, leave_type) for _ in range(2)]

    barrier = asyncio.Barrier(2)
    original = TimeOffRequestService._matching_allocation
    versions_read = []

    async def synchronized(self, row):
        allocation_row = await original(self, row)
        versions_read.append(allocation_row.version)
        await asyncio.wait_for(barrier.wait(), 10)
        return allocation_row

    monkeypatch.setattr(TimeOffRequestService, "_matching_allocation", synchronized)

    async def approve(row):
        async with AsyncSessionLocal() as session:
            try:
                result = await TimeOffRequestService(session).decide(
                    row["id"],
                    Decision(version=row["version"]),
                    CurrentUser("hr.manager@peoplepay360.com", UserRole.HR_MANAGER),
                    approve=True,
                )
                await session.commit()
                return ("ok", result.status.value)
            except HTTPException as exc:
                await session.rollback()
                return ("refused", exc.status_code, str(exc.detail))
            except Exception as exc:  # noqa: BLE001 - the point of the test
                await session.rollback()
                return ("crashed", type(exc).__name__, str(exc))

    outcomes = await asyncio.wait_for(
        asyncio.gather(*(approve(row) for row in requests)), 20
    )

    crashed = [o for o in outcomes if o[0] == "crashed"]
    assert not crashed, f"the race produced an uncontrolled failure: {crashed}"

    winners = [o for o in outcomes if o[0] == "ok"]
    losers = [o for o in outcomes if o[0] == "refused"]
    assert len(winners) == 1 and len(losers) == 1, f"expected 1 win / 1 refusal, got {outcomes}"
    assert winners[0][1] == "approved"
    assert losers[0][1] == 409, "the loser must be a conflict, not a server error"
    assert "refresh" in losers[0][2].lower() or "concurrent" in losers[0][2].lower(), (
        f"the loser's message does not tell the user what to do: {losers[0][2]}"
    )

    # Both readers genuinely saw the same version — otherwise this was a
    # sequential test wearing a race's clothes.
    assert versions_read == [alloc["version"], alloc["version"]]

    # No double debit, checked against the row itself.
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(TimeOffAllocation).where(
                    TimeOffAllocation.id == internal_id(alloc["id"], "alloc")
                )
            )
        ).scalar_one()
        assert row.taken == Decimal("1.00"), f"taken is {row.taken}, expected exactly 1.00"
        assert row.taken <= row.allocated, "taken exceeded allocated — balance corrupted"


# ==========================================================================
# 3. Overlapping / invalid contract creation
# ==========================================================================


async def test_3_overlapping_and_invalid_contracts_are_refused_without_writing(
    client, cleanup_employees
):
    """Payroll's "exactly one contract per period" is a DATABASE guarantee.

    Three attacks: an overlap in the middle, an overlap on a single shared day
    (the `'[]'` inclusive bound, the one most likely to be got wrong), and an
    inverted date range. Each must be refused with an explanation naming the
    conflict, and each must leave the contract table exactly as it was —
    a rejected INSERT that still committed would break the uniqueness payroll
    relies on when resolving a period.
    """
    employee = await make_employee(client)
    employee_pk = internal_id(employee["id"], "emp")
    first = await make_contract(
        client, employee["id"], wage="30000.00", start_date="2026-01-01", end_date="2026-06-30"
    )
    before = await count_rows(Contract, employee_id=employee_pk)

    attacks = [
        ("mid-range overlap", {"start_date": "2026-03-01", "end_date": "2026-09-30"}, 409,
         ("active contract",)),
        # 2026-06-30 is `first`'s last day. daterange(..., '[]') is inclusive at
        # BOTH ends, so a contract starting that same day overlaps by one day.
        ("single shared day", {"start_date": "2026-06-30", "end_date": "2026-12-31"}, 409,
         ("active contract",)),
        ("inverted dates", {"start_date": "2026-09-30", "end_date": "2026-09-01"}, 422,
         ("end_date",)),
    ]

    for label, dates, expected, must_mention in attacks:
        response = await client.post(
            f"{BASE}/contracts/",
            json={
                "employee_id": employee["id"],
                "wage": "99000.00",
                "status": "active",
                **dates,
            },
            headers=HR_MANAGER,
        )
        assert_controlled(response, expected, context=label)
        assert_explainable(response, must_mention=must_mention, context=label)
        assert await count_rows(Contract, employee_id=employee_pk) == before, (
            f"{label}: refused, but a contract row was written anyway"
        )

    # The day AFTER the first contract ends is legitimate and must still work —
    # a guard that refuses everything is not a guard, it is an outage.
    ok = await client.post(
        f"{BASE}/contracts/",
        json={
            "employee_id": employee["id"],
            "wage": "36000.00",
            "status": "active",
            "start_date": "2026-07-01",
            "end_date": None,
        },
        headers=HR_MANAGER,
    )
    assert ok.status_code == 201, ok.text
    assert first["id"] != ok.json()["id"]


# ==========================================================================
# 4. Missing bank details
# ==========================================================================


async def test_4_missing_bank_details_warns_computes_and_blocks_validation(
    client, cleanup_employees, cleanup_schedules, cleanup_salary_config, cleanup_payroll
):
    """Missing bank details must NOT stop the arithmetic — it must stop the
    payment.

    The wrong controlled behaviours here are as bad as a crash: refusing to
    compute the payrun at all (one incomplete record blocks everyone's payroll),
    or computing and letting it be validated (payroll finalized for someone who
    cannot be paid). The right one is: compute, warn BLOCKING, refuse Validate
    with a report naming the employee, and pass once the record is fixed AND
    recomputed.
    """
    employee = await make_employee(client, bank_account=None)
    await attach_working_schedule(client, employee["id"])
    await make_contract(client, employee["id"], wage="30000.00")
    structure = await standard_structure(client)
    payrun = await make_payrun(client, structure["id"], [employee["id"]])

    result = (await compute(client, payrun)).json()
    assert result["computed_count"] == 1, "the payslip must still be computed"
    assert result["blocking_issues"].get("missing_bank_details") == 1

    slips = (
        await client.get(f"{BASE}/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()["items"]
    warning = next(w for w in slips[0]["warnings"] if w["code"] == "missing_bank_details")
    assert warning["severity"] == "blocking"
    assert employee["id"] in warning["references"], (
        "the warning must name the record to open"
    )

    current = await payrun_now(client, payrun)
    refused = await client.post(
        f"{BASE}/payruns/{payrun['id']}/validate",
        json={"version": current["version"]},
        headers=PAYROLL_USER,
    )
    assert_controlled(refused, 409, context="validate with missing bank details")
    assert_explainable(
        refused,
        must_mention=("missing_bank_details",),
        context="validate with missing bank details",
    )

    # Fixing the record alone must NOT unblock it: the payslip still carries the
    # warning it was computed with. This is the "silent corruption" direction —
    # validating against a stale finding.
    await client.patch(
        f"{BASE}/employees/{employee['id']}",
        json={"bank_account": "GB00FIXED0001"},
        headers=HR_MANAGER,
    )
    current = await payrun_now(client, payrun)
    still = await client.post(
        f"{BASE}/payruns/{payrun['id']}/validate",
        json={"version": current["version"]},
        headers=PAYROLL_USER,
    )
    assert_controlled(still, 409, context="validate after fix but before recompute")

    await compute(client, await payrun_now(client, payrun))
    current = await payrun_now(client, payrun)
    passed = await client.post(
        f"{BASE}/payruns/{payrun['id']}/validate",
        json={"version": current["version"]},
        headers=PAYROLL_USER,
    )
    assert passed.status_code == 200, passed.text
    assert passed.json()["blocking_count"] == 0


# ==========================================================================
# 5. Insufficient leave balance
# ==========================================================================


async def test_5_insufficient_balance_refuses_and_leaves_no_partial_debit(
    client, leave_setup
):
    """Approving more leave than an allocation holds must debit NOTHING.

    The corruption to catch is a partial write: the allocation debited (or
    left mid-transaction) while the request stays pending, so the balance
    silently drops without any approved absence to account for it. Phase 2
    approves inside a savepoint precisely so both roll back together.
    """
    employee, _, leave_type, _ = leave_setup
    alloc = await allocation(client, employee, leave_type, "1")
    # Three days against one day of balance.
    oversized = await make_leave_request(
        client, employee, leave_type, date_from="2026-09-07", date_to="2026-09-09"
    )

    response = await decide(client, oversized)
    assert_controlled(response, 409, context="approve beyond balance")
    assert_explainable(
        response, must_mention=("balance",), context="approve beyond balance"
    )

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(TimeOffAllocation).where(
                    TimeOffAllocation.id == internal_id(alloc["id"], "alloc")
                )
            )
        ).scalar_one()
    assert row.taken == Decimal("0.00"), (
        f"a refused approval debited {row.taken} from the allocation"
    )
    assert row.version == alloc["version"], "the allocation row was written during a refusal"

    still_pending = (
        await client.get(f"{BASE}/time-off-requests/{oversized['id']}", headers=HR_MANAGER)
    ).json()
    assert still_pending["status"] == "to_approve", (
        "the request moved out of pending despite the approval being refused"
    )

    # A request that DOES fit still works — the guard is a balance check, not a
    # blanket refusal.
    fitting = await make_leave_request(client, employee, leave_type)
    assert (await decide(client, fitting)).status_code == 200


# ==========================================================================
# 6. Invalid salary formula, refused at STRUCTURE-SAVE time
# ==========================================================================


@pytest.mark.parametrize(
    "label, expression, expected_status",
    [
        # Forward reference: NET runs before the rule it reads.
        ("forward reference", "__LATER__ + 1", 400),
        # A name nothing defines.
        ("unknown name", "NOT_A_REAL_INPUT * 2", 400),
        # Injection shapes — refused for containing a Call node at all, at
        # RULE-save time, before any structure exists.
        ("call injection", "__import__('os').system('echo pwned')", 422),
        ("attribute access", "CONTRACT_WAGE.__class__", 422),
        ("subscript", "CONTRACT_WAGE[0]", 422),
        ("not an expression", "= = =", 422),
    ],
)
async def test_6_invalid_formula_is_refused_before_any_payrun_exists(
    client, cleanup_salary_config, label, expression, expected_status
):
    """A structure that cannot resolve must be refused at SAVE time.

    This is the load-bearing property behind Phase 4 never handling a
    forward-reference failure at compute time: by the time a payrun runs, every
    saved structure is already known-resolvable. If any of these saved
    successfully, the failure would surface mid-payroll instead — with some
    employees computed and some not.

    Injection shapes are refused at RULE-save time (422, schema validation);
    ordering mistakes at STRUCTURE-save time (400), because ordering is a
    property of a structure, not of a rule in isolation.
    """
    later = await make_rule(
        client, "LATER", SalaryRuleComputation.FIXED, SalaryRuleCategory.ALLOWANCE, amount="10.00"
    )
    payload = expression.replace("__LATER__", later["code"])

    rule_response = await client.post(
        f"{BASE}/salary-rules/",
        json={
            "name": "Adversarial",
            "code": f"ADV_{uuid.uuid4().hex[:6].upper()}",
            "category": "net",
            "computation_method": "formula",
            "expression": payload,
        },
        headers=PAYROLL_MANAGER,
    )

    if expected_status == 422:
        assert_controlled(rule_response, 422, context=f"rule save: {label}")
        assert_explainable(rule_response, must_mention=("expression",), context=f"rule save: {label}")
        return

    # Shape is legal, so the rule saves; the ORDERING is what must be refused,
    # and only a structure can express ordering.
    assert rule_response.status_code == 201, rule_response.text
    bad_rule = rule_response.json()

    structure_response = await client.post(
        f"{BASE}/salary-structures/",
        json={
            "name": "Adversarial structure",
            "code": f"ADVS_{uuid.uuid4().hex[:6].upper()}",
            # The formula rule runs FIRST, before the rule it references.
            "rules": [
                {"salary_rule_id": bad_rule["id"], "sequence": 10},
                {"salary_rule_id": later["id"], "sequence": 20},
            ],
        },
        headers=PAYROLL_MANAGER,
    )
    assert_controlled(structure_response, 400, context=f"structure save: {label}")
    assert_explainable(
        structure_response,
        must_mention=(bad_rule["code"],),
        context=f"structure save: {label}",
    )


# ==========================================================================
# 7. Mutation against an already-PAID payslip
# ==========================================================================


async def test_7_paid_payslip_resists_every_mutation_path(
    client, cleanup_employees, cleanup_schedules, cleanup_salary_config, cleanup_payroll
):
    """A paid payslip is the record of money that left the account.

    Every route that could rewrite one is attacked in turn, because the way
    this invariant actually breaks is someone remembering the guard on three
    paths and forgetting the fourth. After all of them, the payslip's stored
    figures are re-read from the DATABASE and compared to what they were before
    the attacks.
    """
    employee, _, payrun = await clean_payrun(client)
    await compute(client, payrun)

    current = await payrun_now(client, payrun)
    validated = await client.post(
        f"{BASE}/payruns/{payrun['id']}/validate",
        json={"version": current["version"]},
        headers=PAYROLL_USER,
    )
    assert validated.status_code == 200, validated.text
    current = await payrun_now(client, payrun)
    paid = await client.post(
        f"{BASE}/payruns/{payrun['id']}/mark-paid",
        json={"version": current["version"]},
        headers=PAYROLL_USER,
    )
    assert paid.status_code == 200, paid.text

    slip = (
        await client.get(f"{BASE}/payruns/{payrun['id']}/payslips", headers=PAYROLL_USER)
    ).json()["items"][0]
    assert slip["status"] == PayslipStatus.PAID.value
    slip_pk = internal_id(slip["id"], "pslip")

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(Payslip).where(Payslip.id == slip_pk))
        ).scalar_one()
        before = (row.gross_amount, row.net_amount, row.worked_days, row.status, row.version)
        line_count_before = await count_rows(PayslipLine, payslip_id=slip_pk)

    current = await payrun_now(client, payrun)
    attacks = [
        (
            "recompute",
            client.post(
                f"{BASE}/payruns/{payrun['id']}/compute",
                json={"version": current["version"]},
                headers={**PAYROLL_USER, **idem()},
            ),
            ("recomputed",),
        ),
        (
            "rename the payrun",
            client.patch(
                f"{BASE}/payruns/{payrun['id']}",
                json={"name": "Renamed after payment", "version": current["version"]},
                headers=PAYROLL_USER,
            ),
            ("paid",),
        ),
        (
            "delete the payrun",
            client.delete(
                f"{BASE}/payruns/{payrun['id']}?version={current['version']}",
                headers=PAYROLL_MANAGER,
            ),
            ("paid",),
        ),
        (
            "delete the payslip",
            client.delete(
                f"{BASE}/payslips/{slip['id']}?version={slip['version']}",
                headers=PAYROLL_MANAGER,
            ),
            ("immutable",),
        ),
    ]
    for label, coroutine, must_mention in attacks:
        response = await coroutine
        assert_controlled(response, 409, context=f"paid payslip: {label}")
        assert_explainable(response, must_mention=must_mention, context=f"paid payslip: {label}")

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(Payslip).where(Payslip.id == slip_pk))
        ).scalar_one()
        after = (row.gross_amount, row.net_amount, row.worked_days, row.status, row.version)
        assert row.deleted_at is None, "a paid payslip was soft-deleted"
    assert after == before, f"the paid payslip changed under attack: {before} -> {after}"
    assert await count_rows(PayslipLine, payslip_id=slip_pk) == line_count_before


# ==========================================================================
# 8. LOP_AMOUNT with zero scheduled working days
# ==========================================================================


async def test_8_zero_working_days_warns_and_never_divides_by_zero(
    client, cleanup_employees, cleanup_schedules, cleanup_salary_config, cleanup_payroll
):
    """`LOP_AMOUNT = (CONTRACT_WAGE / SCHEDULE_WORKING_DAYS) * UNPAID_LEAVE_DAYS`
    has a zero in the denominator whenever the period contains no scheduled
    working day. Gap-fix's documented policy is to OMIT `LOP_AMOUNT` and raise
    a BLOCKING `lop_schedule_unavailable` warning — never to divide, and never
    to substitute a fabricated zero.

    Attacked with a schedule that is real and non-empty but has NO day falling
    inside the payrun period: a Sunday-only schedule over a Monday-to-Saturday
    week. That is nastier than "no schedule at all", because every naive guard
    (`if schedule is None`) passes it straight through to the division.

    Both directions are checked, because "no crash" alone is not enough:
      * a structure that CONSUMES LOP must not produce a payslip built on a
        fabricated number;
      * the warning must reach the firewall and block Validate.
    """
    # Monday 2026-09-07 .. Saturday 2026-09-12 — contains no Sunday.
    period_start, period_end = "2026-09-07", "2026-09-12"

    employee = await make_employee(client)
    sunday_only = await client.post(
        f"{BASE}/working-schedules/",
        json={
            "name": f"Sundays only {uuid.uuid4().hex[:6]}",
            "lines": [
                {
                    "day_of_week": "sunday",
                    "start_time": "09:00",
                    "end_time": "17:00",
                    "break_minutes": 0,
                }
            ],
        },
        headers=HR_MANAGER,
    )
    assert sunday_only.status_code == 201, sunday_only.text
    await client.patch(
        f"{BASE}/employees/{employee['id']}",
        json={"default_schedule_id": sunday_only.json()["id"]},
        headers=HR_MANAGER,
    )
    await make_contract(
        client, employee["id"], wage="30000.00", start_date="2026-01-01", end_date=None
    )

    lop_rule = await make_rule(
        client,
        "ZERODAY_LOP",
        SalaryRuleComputation.FORMULA,
        SalaryRuleCategory.DEDUCTION,
        expression="LOP_AMOUNT",
    )
    structure = await make_structure(client, [(lop_rule, 10)])

    payrun_response = await client.post(
        f"{BASE}/payruns/",
        json={
            "name": "Zero working days",
            "salary_structure_id": structure["id"],
            "period_start": period_start,
            "period_end": period_end,
            "employee_ids": [employee["id"]],
        },
        headers={**PAYROLL_USER, **idem()},
    )
    assert payrun_response.status_code == 201, payrun_response.text
    payrun = payrun_response.json()

    computed = await client.post(
        f"{BASE}/payruns/{payrun['id']}/compute",
        json={"version": payrun["version"]},
        headers={**PAYROLL_USER, **idem()},
    )
    # Controlled: the run does not 500, whatever else it decides.
    assert computed.status_code != 500, (
        f"zero scheduled working days crashed the compute path — this is the "
        f"divide-by-zero the warning exists to prevent. Body: {computed.text[:400]}"
    )
    assert computed.status_code == 200, computed.text
    body = computed.json()

    # Explainable: the finding names the schedule as the thing to fix.
    text = computed.text.lower()
    assert "lop_schedule_unavailable" in text, (
        f"no lop_schedule_unavailable finding was produced: {computed.text[:600]}"
    )
    assert "schedule" in text

    # Uncorrupted: no payslip was built on a fabricated LOP.
    payrun_pk = internal_id(payrun["id"], "prun")
    assert await count_rows(Payslip, payrun_id=payrun_pk) == 0, (
        "a payslip was written for an employee whose LOP_AMOUNT could not be "
        "determined — the structure consumes LOP, so any figure here is invented"
    )
    assert body["computed_count"] == 0

    # And the firewall refuses to finalize.
    current = await payrun_now(client, payrun)
    validate = await client.post(
        f"{BASE}/payruns/{payrun['id']}/validate",
        json={"version": current["version"]},
        headers=PAYROLL_USER,
    )
    assert_controlled(validate, 409, context="validate with undetermined LOP")
    assert_explainable(
        validate, must_mention=("lop_schedule_unavailable",), context="validate with undetermined LOP"
    )


async def test_8b_zero_working_days_is_computed_by_the_real_context_builder(
    client, cleanup_employees, cleanup_schedules
):
    """The same edge case one level down, against the context builder itself.

    Asserted directly rather than only through HTTP so the finding is pinned to
    the arithmetic rather than to a route: with zero working days the seed must
    OMIT `LOP_AMOUNT` entirely — not carry a zero, not carry `None`, either of
    which a downstream formula would happily consume.
    """
    from app.models.employee import Employee
    from app.services.payroll_context import build_payroll_context

    employee = await make_employee(client)
    sunday_only = await client.post(
        f"{BASE}/working-schedules/",
        json={
            "name": f"Sundays only ctx {uuid.uuid4().hex[:6]}",
            "lines": [
                {
                    "day_of_week": "sunday",
                    "start_time": "09:00",
                    "end_time": "17:00",
                    "break_minutes": 0,
                }
            ],
        },
        headers=HR_MANAGER,
    )
    await client.patch(
        f"{BASE}/employees/{employee['id']}",
        json={"default_schedule_id": sunday_only.json()["id"]},
        headers=HR_MANAGER,
    )
    contract = await make_contract(
        client, employee["id"], wage="30000.00", start_date="2026-01-01", end_date=None
    )

    async with AsyncSessionLocal() as session:
        employee_row = (
            await session.execute(
                select(Employee).where(Employee.id == internal_id(employee["id"], "emp"))
            )
        ).scalar_one()
        contract_row = (
            await session.execute(
                select(Contract).where(Contract.id == internal_id(contract["id"], "ctr"))
            )
        ).scalar_one()

        context = await build_payroll_context(
            session,
            employee_row,
            contract_row,
            date(2026, 9, 7),
            date(2026, 9, 12),
        )

    assert "LOP_AMOUNT" not in context.seed, (
        f"LOP_AMOUNT was populated with {context.seed.get('LOP_AMOUNT')!r} despite zero "
        "scheduled working days — a fabricated number is worse than a refusal"
    )
    assert context.lop_warning is not None
    assert context.lop_warning.code == "lop_schedule_unavailable"
    assert context.lop_warning.severity == "blocking"
    # The other inputs are still usable: the failure is scoped to LOP, not to
    # the whole context.
    assert context.seed["CONTRACT_WAGE"] == Decimal("30000.00")
