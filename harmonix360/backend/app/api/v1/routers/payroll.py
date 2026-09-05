"""Payruns and payslips (PS B5-B7).

RBAC (Architecture §5 / PRD §3), and the single most important denial in the
whole matrix:

    HR Payroll User     Create / Read / Update on Payruns and Payslips
    HR Payroll Manager  the above + Delete
    HR Manager          NO ACCESS AT ALL — not even read
    Employee            NO ACCESS AT ALL

HR Manager's exclusion is what keeps "can manage people" separate from "can see
and change what they are paid", so every route below is gated on
`PAYROLL_ROLES` (which does not contain HR_MANAGER) and the two destructive
ones on `PAYROLL_ADMIN_ROLES`.

Employees do not read their own payslips here either. PRD §3 gives the Employee
role "own profile, attendance, leave balances" and stops; payslip delivery to
the employee is PS B8's emailed PDF, not a self-service endpoint. Adding one
would be a scope decision, not an oversight to quietly correct.

Compute / Validate / Mark Paid / Send Payslips are POSTs to named
sub-resources rather than a PATCH that sets `status` directly. A payrun's
status is not a field a client assigns — each transition runs its own
preconditions (the advisory lock, the blocking-issue gate, the finality wall),
and a settable status field is an invitation to skip them.
"""
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import CurrentUser, require_idempotency_key, require_role
from app.core.database import get_db
from app.core.rate_limit import rate_limiter
from app.models.enums import PAYROLL_ADMIN_ROLES, PAYROLL_ROLES, PayrunStatus
from app.models.payroll import Payrun, Payslip
from app.schemas.common import PaginatedResponse
from app.schemas.employee import EmployeeRef
from app.schemas.payroll import (
    EligibleEmployee,
    PayrunComputeResponse,
    PayrunCreate,
    PayrunResponse,
    PayrunTransition,
    PayrunUpdate,
    PayrunValidationReport,
    PayslipDeliveryResponse,
    PayslipResponse,
)
from app.services.payroll import PayrunService, PayslipService

router = APIRouter(dependencies=[Depends(rate_limiter)])

payruns_router = APIRouter(prefix="/payruns", tags=["Payroll"])
payslips_router = APIRouter(prefix="/payslips", tags=["Payroll"])


# --------------------------------------------------------------- serialisers


def _payrun_response(payrun: Payrun, payslip_counts: dict[int, int]) -> PayrunResponse:
    response = PayrunResponse.model_validate(payrun)
    # SERVER-COMPUTED, set after `model_validate` — the same two-step pattern
    # as `ContractResponse.is_currently_active` and
    # `SalaryStructureResponse.rule_count`. Neither field is a column: the
    # selection is a collection of link rows, and the payslip count comes from
    # one grouped query over the whole page.
    response.employees = [EmployeeRef.model_validate(link.employee) for link in payrun.selected_employees]
    response.payslip_count = payslip_counts.get(payrun.id, 0)
    return response


async def _respond_payruns(service: PayrunService, payruns: List[Payrun]) -> List[PayrunResponse]:
    counts = await service.payslip_counts(payruns)
    return [_payrun_response(payrun, counts) for payrun in payruns]


def _payslip_response(payslip: Payslip) -> PayslipResponse:
    # `payrun` comes straight off the relationship — every payslip read path
    # loads it eagerly (`_PAYSLIP_LOADS`), so no lazy load happens inside
    # Pydantic's synchronous attribute access.
    return PayslipResponse.model_validate(payslip)


# ------------------------------------------------------------------- payruns


@payruns_router.get("/", response_model=PaginatedResponse[PayrunResponse], summary="List payruns")
async def list_payruns(
    payrun_status: Optional[PayrunStatus] = Query(default=None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    service = PayrunService(db)
    rows, total = await service.list_payruns(limit=limit, offset=offset, payrun_status=payrun_status)
    return PaginatedResponse(
        items=await _respond_payruns(service, rows), total=total, limit=limit, offset=offset
    )


@payruns_router.get(
    "/eligible-employees",
    response_model=PaginatedResponse[EligibleEmployee],
    summary="Employees with exactly one active contract covering the period (PS B5 step 2)",
)
async def eligible_employees(
    period_start: date,
    period_end: date,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    """Declared BEFORE `/{public_id}`, or "eligible-employees" is read as a
    payrun id — the same ordering trap the frontend router documents for
    `employees/schedules`."""
    start, end = period_start, period_end
    service = PayrunService(db)
    rows, total = await service.eligible_employees(start, end, limit=limit, offset=offset)
    items = [
        EligibleEmployee(
            employee=EmployeeRef.model_validate(employee),
            contract_id=contract.public_id,
            wage=contract.wage,
            partial_period=contract.start_date > start
            or (contract.end_date is not None and contract.end_date < end),
        )
        for employee, contract in rows
    ]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@payruns_router.post(
    "/",
    response_model=PayrunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a payrun (PS B5 wizard: structure + period + explicit selection)",
    responses={400: {"description": "The required Idempotency-Key header is missing (Architecture §6)."}},
)
async def create_payrun(
    dto: PayrunCreate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
    idempotency_key: str = Depends(require_idempotency_key),
):
    service = PayrunService(db)
    payrun = await service.create_payrun(dto, actor_email=current_user.email)
    return (await _respond_payruns(service, [payrun]))[0]


@payruns_router.get("/{public_id}", response_model=PayrunResponse)
async def get_payrun(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    service = PayrunService(db)
    payrun = await service.read_payrun(public_id)
    return (await _respond_payruns(service, [payrun]))[0]


@payruns_router.patch("/{public_id}", response_model=PayrunResponse)
async def update_payrun(
    public_id: str,
    dto: PayrunUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    """Name, notes and the employee selection only, and only while the run is
    DRAFT or COMPUTED. The period and structure are fixed at creation — see
    `PayrunUpdate`."""
    service = PayrunService(db)
    payrun = await service.update_payrun(public_id, dto, actor_email=current_user.email)
    return (await _respond_payruns(service, [payrun]))[0]


@payruns_router.delete("/{public_id}", response_model=PayrunResponse)
async def delete_payrun(
    public_id: str,
    version: int = Query(ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ADMIN_ROLES)),
):
    """Delete is the one payrun verb HR Payroll User does not hold (PRD §3:
    Payroll User has CRU, Payroll Manager has full CRUD)."""
    service = PayrunService(db)
    payrun = await service.delete_payrun(public_id, version, actor_email=current_user.email)
    return (await _respond_payruns(service, [payrun]))[0]


@payruns_router.post(
    "/{public_id}/compute",
    response_model=PayrunComputeResponse,
    summary="Compute every selected employee's payslip (Architecture §7)",
    responses={
        400: {"description": "The required Idempotency-Key header is missing (Architecture §6)."},
        409: {
            "description": (
                "The payrun moved on since it was read, is already finalized, or its structure has "
                "no active rules."
            )
        },
    },
)
async def compute_payrun(
    public_id: str,
    dto: PayrunTransition,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
    idempotency_key: str = Depends(require_idempotency_key),
):
    """Runs under `acquire_entity_lock(session, "payrun", id)` and requires an
    `Idempotency-Key` — the two Architecture §6 obligations that apply to this
    endpoint and to no other read on this router."""
    service = PayrunService(db)
    result = await service.compute(public_id, dto.version, actor_email=current_user.email)
    return PayrunComputeResponse(
        payrun=(await _respond_payruns(service, [result["payrun"]]))[0],
        computed_count=len(result["computed"]),
        skipped=result["skipped"],
        blocking_issues=result["blocking"],
    )


@payruns_router.get(
    "/{public_id}/validation",
    response_model=PayrunValidationReport,
    summary="Re-run the blocking-issue checks without changing anything (PRD §5.10 Revalidate)",
)
async def payrun_validation(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    service = PayrunService(db)
    payrun = await service.read_payrun(public_id)
    return PayrunValidationReport(**await service.validation_report(payrun))


@payruns_router.post(
    "/{public_id}/validate",
    response_model=PayrunValidationReport,
    summary="Validate a computed payrun (PS B6)",
    responses={409: {"description": "Blocking issues remain; the body carries the full report."}},
)
async def validate_payrun(
    public_id: str,
    dto: PayrunTransition,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    service = PayrunService(db)
    result = await service.validate_payrun(public_id, dto.version, actor_email=current_user.email)
    return PayrunValidationReport(**result["report"])


@payruns_router.post(
    "/{public_id}/mark-paid",
    response_model=PayrunResponse,
    summary="Mark a validated payrun paid (PS B6) — after this the run is immutable",
)
async def mark_payrun_paid(
    public_id: str,
    dto: PayrunTransition,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    service = PayrunService(db)
    payrun = await service.mark_paid(public_id, dto.version, actor_email=current_user.email)
    return (await _respond_payruns(service, [payrun]))[0]


@payruns_router.post(
    "/{public_id}/send-payslips",
    response_model=PayslipDeliveryResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Hand a paid payrun to the delivery queue (PS B6) — enqueue only",
)
async def send_payslips(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    """202, not 200: this endpoint's whole promise is that the run was
    QUEUED. Rendering the PDFs and sending the mail is PS B8 / Phase 5, being
    built separately — nothing in this phase's code path renders or sends
    anything."""
    service = PayrunService(db)
    result = await service.enqueue_payslip_delivery(public_id, actor_email=current_user.email)
    return PayslipDeliveryResponse(
        payrun_id=result["payrun_id"],
        task_id=result["task_id"],
        payslip_count=result["payslip_count"],
        detail=(
            f"Queued {result['payslip_count']} payslip(s) for delivery. "
            "PDF rendering and email are handled by the payslip-delivery worker (PS B8)."
        ),
    )


@payruns_router.get(
    "/{public_id}/payslips",
    response_model=PaginatedResponse[PayslipResponse],
    summary="This payrun's payslips (PS B7)",
)
async def payrun_payslips(
    public_id: str,
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    service = PayslipService(db)
    rows, total = await service.list_payslips(payrun_id=public_id, limit=limit, offset=offset)
    return PaginatedResponse(
        items=[_payslip_response(row) for row in rows], total=total, limit=limit, offset=offset
    )


# ------------------------------------------------------------------ payslips


@payslips_router.get("/", response_model=PaginatedResponse[PayslipResponse], summary="List payslips")
async def list_payslips(
    payrun_id: Optional[str] = None,
    employee_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    service = PayslipService(db)
    rows, total = await service.list_payslips(
        payrun_id=payrun_id, employee_id=employee_id, limit=limit, offset=offset
    )
    return PaginatedResponse(
        items=[_payslip_response(row) for row in rows], total=total, limit=limit, offset=offset
    )


@payslips_router.get(
    "/{public_id}",
    response_model=PayslipResponse,
    summary="One payslip, rule by rule (PS B7)",
)
async def get_payslip(
    public_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ROLES)),
):
    """The `lines` array is the breakdown PS B7 asks for: one entry per Salary
    Rule that ran, in the sequence it ran, with the category it was filed
    under — every amount traceable to the rule that produced it."""
    service = PayslipService(db)
    return _payslip_response(await service.read_payslip(public_id))


@payslips_router.delete("/{public_id}", response_model=PayslipResponse)
async def delete_payslip(
    public_id: str,
    version: int = Query(ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: CurrentUser = Depends(require_role(PAYROLL_ADMIN_ROLES)),
):
    """Removes one payslip from a run that has not been finalized. Refused
    once the run is VALIDATED or PAID — a payslip is not a document that can
    be withdrawn after it records money that has been paid."""
    service = PayslipService(db)
    return _payslip_response(await service.delete_payslip(public_id, version, actor_email=current_user.email))


router.include_router(payruns_router)
router.include_router(payslips_router)
