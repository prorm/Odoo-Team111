"""Payrun / Payslip request and response shapes (PS B5/B6/B7).

Conventions inherited from earlier phases, restated because payroll is where
breaking them costs the most:

  * every `id` crossing the wire is a `public_id` hashid, and every reference
    to another entity is a NESTED object carrying that hashid — never a raw
    integer FK (app/schemas/contract.py's note on `working_schedule_id`);
  * every amount is `Decimal`, declared `max_digits=12, decimal_places=2` to
    match `Numeric(12,2)`. No `float` appears in this module, and none may:
    Architecture §10 forbids it on the whole payroll path;
  * PATCH bodies are full editable-form replacements carrying a required
    `version`, not sparse patches (progress.md, Phase 2).

WHAT A CLIENT MAY NOT SEND
--------------------------
`worked_days`, `gross_amount`, `net_amount`, `warnings` and every payslip line
are absent from every request schema in this file. They are outputs of the rule
engine, and an input a client can set is an input a client can falsify — the
same reasoning that keeps `Attendance.worked_hours` and
`WorkingSchedule.weekly_hours` server-computed. There is deliberately no
"correct this payslip amount" endpoint: a wrong figure is fixed by fixing its
input or its rule and recomputing the run, which leaves an audit trail, rather
than by typing a different number over the top of it.
"""
from datetime import date
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import PayrunStatus, PayslipStatus, SalaryRuleCategory
from app.schemas.common import ORMModel
from app.schemas.contract import SalaryStructureRef
from app.schemas.employee import EmployeeRef


class ContractRef(ORMModel):
    """The contract a payslip was computed against, as it appears on the
    payslip.

    Carries `wage` because it is the payslip's own `CONTRACT_WAGE` input and
    PS B7 asks for a breakdown a person can follow: showing the rule results
    without the wage they came from makes the top line unexplainable. Anyone
    who can read the payslip can already read the amount it produced.
    """

    id: str = Field(validation_alias="public_id")
    wage: Decimal = Field(max_digits=12, decimal_places=2)
    start_date: date
    end_date: Optional[date] = None
    job_position: Optional[str] = None


# --------------------------------------------------------------------- payrun


class PayrunCreate(BaseModel):
    """PS B5's wizard, as one request: step 1 (structure + period) and step 2
    (explicit employee selection) arrive together, because a payrun with no
    selected employees is not a half-made payrun — it is a run that would
    compute nothing.

    `employee_ids` is REQUIRED and must be non-empty. PS B5 calls the
    selection explicit, and the most tempting shortcut in this whole feature
    is defaulting it to "everyone active": that silently pays people nobody
    chose to pay, and it does so most readily in exactly the month somebody
    joined or left.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=180)
    salary_structure_id: str
    period_start: date
    period_end: date
    notes: Optional[str] = None
    employee_ids: List[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_period_and_selection(self) -> "PayrunCreate":
        if self.period_end < self.period_start:
            raise ValueError("period_end must not precede period_start")
        if len(set(self.employee_ids)) != len(self.employee_ids):
            raise ValueError("an employee may be selected at most once in a payrun")
        return self


class PayrunUpdate(BaseModel):
    """Full editable-form replacement with a required `version`.

    The period and the structure are NOT editable. Changing either would make
    the run's already-computed payslips answer a different question than the
    one they were computed for, and the fix — create the run you actually
    meant — costs one request and leaves the first run's history intact.
    Omitting `employee_ids` leaves the selection alone; sending it replaces
    the whole set, the same replace-the-ordered-collection convention as
    `SalaryStructureUpdate.rules` and `WorkingScheduleUpdate.lines`.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=180)
    notes: Optional[str] = None
    employee_ids: Optional[List[str]] = Field(default=None, min_length=1)
    version: int

    @model_validator(mode="after")
    def _no_duplicate_employees(self) -> "PayrunUpdate":
        if self.employee_ids is not None and len(set(self.employee_ids)) != len(self.employee_ids):
            raise ValueError("an employee may be selected at most once in a payrun")
        return self


class PayrunTransition(BaseModel):
    """Body for Compute / Validate / Mark Paid.

    `version` is required on all three for the same reason it is required on
    every other mutation in this codebase: these are state transitions on a
    row somebody else may have moved since the screen was loaded, and
    "Mark Paid" is the least forgiving place in the product to act on a stale
    read.
    """

    model_config = ConfigDict(extra="forbid")

    version: int


class PayrunResponse(ORMModel):
    id: str = Field(validation_alias="public_id")
    name: str
    period_start: date
    period_end: date
    status: PayrunStatus
    notes: Optional[str] = None
    salary_structure: SalaryStructureRef
    #: The explicit selection from PS B5 step 2, as employee references.
    #: SERVER-COMPUTED in the router from `selected_employees`, which is a
    #: collection of link rows rather than of employees.
    employees: List[EmployeeRef] = Field(default_factory=list)
    #: How many payslips this run currently holds. Zero on a DRAFT run, and
    #: the difference between it and `len(employees)` is exactly the set of
    #: selected employees Compute could not produce a payslip for.
    payslip_count: int = 0
    version: int


class PayrunSkippedEmployee(BaseModel):
    """A selected employee Compute could not produce a payslip for.

    Not persisted anywhere, and deliberately so: the persistent fact is that
    the employee is selected and has no payslip, which Validate re-derives
    from `payrun_employees` and `payslips` rather than trusting a stored
    summary that a later recompute could leave stale. This shape exists to
    tell the person who just pressed Compute what happened.
    """

    employee_id: str
    employee_name: str
    reason: str


class PayrunComputeResponse(BaseModel):
    """What Compute reports back (Architecture §7, end to end)."""

    payrun: PayrunResponse
    computed_count: int
    skipped: List[PayrunSkippedEmployee] = Field(default_factory=list)
    #: Blocking findings across every payslip in the run, counted by code.
    #: This is PRD §5.10's gate, returned at the moment it becomes actionable.
    blocking_issues: dict[str, int] = Field(default_factory=dict)


class PayrunValidationIssue(BaseModel):
    code: str
    severity: str
    message: str
    references: List[str] = Field(default_factory=list)
    #: The payslip the finding sits on, when it sits on one. A selected
    #: employee with no payslip has a finding and no payslip to hang it from.
    payslip_id: Optional[str] = None
    employee_id: Optional[str] = None


class PayrunValidationReport(BaseModel):
    """PRD §5.10's pre-finalization gate, as data.

    Returned by the Revalidate read AND carried in the 409 body when Validate
    refuses, so the client renders one shape either way.
    """

    payrun_id: str
    status: PayrunStatus
    blocking_count: int
    advisory_count: int
    blocking_by_code: dict[str, int] = Field(default_factory=dict)
    issues: List[PayrunValidationIssue] = Field(default_factory=list)


class PayslipDeliveryResponse(BaseModel):
    """The Send Payslips enqueue boundary (PS B6's fourth action).

    This phase hands the run to the queue and stops there. PDF rendering and
    bulk email are PS B8 / Phase 5, being implemented separately; nothing in
    this phase renders, formats or sends anything.
    """

    payrun_id: str
    task_id: str
    payslip_count: int
    detail: str


# -------------------------------------------------------------------- payslip


class PayslipLineResponse(ORMModel):
    """One Salary Rule's contribution — PS B7's rule-by-rule breakdown.

    `code`, `name` and `category` are read from the LINE, not from the rule it
    came from: they were copied at compute time precisely so a payslip keeps
    rendering the rule as it was when it ran, even after the rule is renamed
    or deleted (app/models/payroll.py).
    """

    id: str = Field(validation_alias="public_id")
    code: str
    name: str
    category: SalaryRuleCategory
    sequence: int
    amount: Decimal = Field(max_digits=12, decimal_places=2)


class PayslipWarningResponse(BaseModel):
    code: str
    severity: str
    message: str
    references: List[str] = Field(default_factory=list)


class PayrunRef(ORMModel):
    """The run a payslip belongs to, reduced to what a payslip screen needs to
    label and date it."""

    id: str = Field(validation_alias="public_id")
    name: str
    period_start: date
    period_end: date
    status: PayrunStatus


class PayslipResponse(ORMModel):
    id: str = Field(validation_alias="public_id")
    employee: EmployeeRef
    contract: ContractRef
    worked_days: Decimal = Field(max_digits=6, decimal_places=2)
    gross_amount: Decimal = Field(max_digits=12, decimal_places=2)
    net_amount: Decimal = Field(max_digits=12, decimal_places=2)
    status: PayslipStatus
    warnings: List[PayslipWarningResponse] = Field(default_factory=list)
    lines: List[PayslipLineResponse] = Field(default_factory=list)
    #: The run this payslip belongs to. A payslip list is unreadable without
    #: it, but the WHOLE payrun (with its own employee list) repeated on every
    #: row would be a page of duplicated JSON — hence a reference, not the
    #: full object. Populated straight from the `payrun` relationship, which
    #: every payslip read path loads eagerly.
    payrun: PayrunRef
    version: int


class EligibleEmployee(BaseModel):
    """A candidate for PS B5 step 2's selection list.

    Eligibility is "has exactly one active contract covering the period", the
    same question Architecture §7 step 1 asks at compute time — so the wizard
    offers the people the engine can actually pay, rather than every active
    employee with the disappointment deferred to Compute. `contract_id` is
    included so the screen can show what they will be paid against without a
    second request per row.
    """

    employee: EmployeeRef
    contract_id: str
    wage: Decimal = Field(max_digits=12, decimal_places=2)
    #: True when the contract does not span the whole period — the wizard can
    #: warn before selection rather than after Compute.
    partial_period: bool = False
