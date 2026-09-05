"""
Transfer Request workflow state machine definition.

States: PENDING → AI_REVIEWING → APPROVED / REJECTED / PENDING_REVIEW → COMPLETED
PENDING_REVIEW = AI was unavailable or uncertain, human must override.
"""
from app.workflows.engine import StateMachineDefinition
from app.models.enums import TransferStatus, UserRole


def build_transfer_workflow() -> StateMachineDefinition:
    wf = StateMachineDefinition(name="transfer-request", initial_state=TransferStatus.PENDING.value)

    wf.add_state(TransferStatus.PENDING.value, is_initial=True)
    wf.add_state(TransferStatus.AI_REVIEWING.value)
    wf.add_state(TransferStatus.APPROVED.value)
    wf.add_state(TransferStatus.REJECTED.value)
    wf.add_state(TransferStatus.PENDING_REVIEW.value)
    wf.add_state(TransferStatus.COMPLETED.value, is_terminal=True)

    # Submit for AI review
    wf.add_transition(
        name="SUBMIT_FOR_REVIEW",
        from_states=[TransferStatus.PENDING.value],
        to_state=TransferStatus.AI_REVIEWING.value,
        roles=[UserRole.EMPLOYEE, UserRole.ASSET_MANAGER, UserRole.ADMIN],
    )

    # AI decisions (system-driven transitions)
    wf.add_transition(
        name="AI_APPROVE",
        from_states=[TransferStatus.AI_REVIEWING.value],
        to_state=TransferStatus.APPROVED.value,
        roles=[UserRole.ADMIN],  # System acts as ADMIN
    )
    wf.add_transition(
        name="AI_REJECT",
        from_states=[TransferStatus.AI_REVIEWING.value],
        to_state=TransferStatus.REJECTED.value,
        roles=[UserRole.ADMIN],
    )
    wf.add_transition(
        name="AI_ESCALATE",
        from_states=[TransferStatus.AI_REVIEWING.value],
        to_state=TransferStatus.PENDING_REVIEW.value,
        roles=[UserRole.ADMIN],
    )

    # Human override — can override from PENDING_REVIEW, APPROVED, or REJECTED
    wf.add_transition(
        name="HUMAN_APPROVE",
        from_states=[TransferStatus.PENDING_REVIEW.value, TransferStatus.REJECTED.value],
        to_state=TransferStatus.APPROVED.value,
        roles=[UserRole.ASSET_MANAGER, UserRole.ADMIN],
    )
    wf.add_transition(
        name="HUMAN_REJECT",
        from_states=[TransferStatus.PENDING_REVIEW.value, TransferStatus.APPROVED.value],
        to_state=TransferStatus.REJECTED.value,
        roles=[UserRole.ASSET_MANAGER, UserRole.ADMIN],
    )

    # Complete an approved transfer
    wf.add_transition(
        name="COMPLETE",
        from_states=[TransferStatus.APPROVED.value],
        to_state=TransferStatus.COMPLETED.value,
        roles=[UserRole.ASSET_MANAGER, UserRole.ADMIN],
    )

    return wf


transfer_workflow = build_transfer_workflow()
