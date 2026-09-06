"""The shared read/print boundary for persisted payslips.

References are captured at compute, never reconstructed on read. Line amounts
come from PayslipLine; totals come from Payslip. Future PDF workers must use
this serializer too. A pre-migration row has no historical input snapshot: its
current contract is not evidence of the original wage. Such reads fail clearly
until a draft/computed run is recomputed or historical evidence is restored.
Never backfill a finalized record from live references and call it history.
"""

from fastapi import HTTPException

from app.schemas.employee import EmployeeRef
from app.schemas.payroll import ContractRef, PayrunRef, PayslipResponse


def capture_references(employee, contract, payrun):
    return {
        "employee": EmployeeRef.model_validate(employee).model_dump(
            mode="json", by_alias=True
        ),
        "contract": ContractRef.model_validate(contract).model_dump(
            mode="json", by_alias=True
        ),
        "payrun": PayrunRef.model_validate(payrun).model_dump(
            mode="json", by_alias=True
        ),
    }


def payslip_response(payslip):
    snapshot = payslip.reference_snapshot
    if snapshot is None:
        raise HTTPException(
            409,
            {
                "code": "historical_snapshot_unavailable",
                "payslip_id": payslip.public_id,
                "message": "This legacy payslip has no historical reference snapshot. Recompute only if unfinalized; finalized records require historical evidence, never live contract backfill.",
            },
        )

    # Validation aliases expect public_id. Snapshots intentionally store public
    # identifiers, never numeric foreign keys. Status is a legitimate payslip
    # lifecycle transition, mirrored in the reference to its parent run.
    def reference(key):
        values = dict(snapshot[key])
        values["public_id"] = values.pop("id")
        # The payrun reference nests the salary structure, which is itself keyed
        # on `public_id`. Rewriting only the outer id would leave the nested ref
        # unvalidatable, so the rename recurses one level. Absent on snapshots
        # captured before the structure was recorded — left absent rather than
        # resolved from the live run.
        nested = values.get("salary_structure")
        if isinstance(nested, dict) and "id" in nested:
            nested = dict(nested)
            nested["public_id"] = nested.pop("id")
            values["salary_structure"] = nested
        return values

    return PayslipResponse.model_validate(
        {
            "public_id": payslip.public_id,
            "employee": reference("employee"),
            "contract": reference("contract"),
            "payrun": {**reference("payrun"), "status": payslip.status.value},
            **{
                key: getattr(payslip, key)
                for key in (
                    "worked_days",
                    "gross_amount",
                    "net_amount",
                    "status",
                    "warnings",
                    "lines",
                    "version",
                )
            },
        }
    )
