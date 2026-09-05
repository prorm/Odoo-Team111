from app.workflows.engine import StateMachineDefinition
from app.models.enums import AssetStatus, UserRole

def build_asset_workflow() -> StateMachineDefinition:
    wf = StateMachineDefinition(name="asset-lifecycle", initial_state=AssetStatus.AVAILABLE.value)
    
    wf.add_state(AssetStatus.AVAILABLE.value, is_initial=True)
    wf.add_state(AssetStatus.ALLOCATED.value)
    wf.add_state(AssetStatus.RESERVED.value)
    wf.add_state(AssetStatus.MAINTENANCE.value)
    wf.add_state(AssetStatus.LOST.value)
    wf.add_state(AssetStatus.RETIRED.value)
    wf.add_state(AssetStatus.DISPOSED.value, is_terminal=True)

    wf.add_transition(
        name="ALLOCATE",
        from_states=[AssetStatus.AVAILABLE.value],
        to_state=AssetStatus.ALLOCATED.value,
        roles=[UserRole.ASSET_MANAGER, UserRole.ADMIN]
    )
    wf.add_transition(
        name="RETURN",
        from_states=[AssetStatus.ALLOCATED.value],
        to_state=AssetStatus.AVAILABLE.value,
        roles=[UserRole.EMPLOYEE, UserRole.ASSET_MANAGER, UserRole.ADMIN]
    )
    wf.add_transition(
        name="MAINTENANCE",
        from_states=[AssetStatus.AVAILABLE.value, AssetStatus.ALLOCATED.value],
        to_state=AssetStatus.MAINTENANCE.value,
        roles=[UserRole.ASSET_MANAGER, UserRole.ADMIN]
    )
    wf.add_transition(
        name="RESTORE",
        from_states=[AssetStatus.MAINTENANCE.value],
        to_state=AssetStatus.AVAILABLE.value,
        roles=[UserRole.ASSET_MANAGER, UserRole.ADMIN]
    )
    wf.add_transition(
        name="RETIRE",
        from_states=[AssetStatus.AVAILABLE.value],
        to_state=AssetStatus.RETIRED.value,
        roles=[UserRole.ADMIN]
    )
    wf.add_transition(
        name="DISPOSE",
        from_states=[AssetStatus.RETIRED.value],
        to_state=AssetStatus.DISPOSED.value,
        roles=[UserRole.ADMIN]
    )
    wf.add_transition(
        name="MARK_LOST",
        from_states=[AssetStatus.AVAILABLE.value, AssetStatus.ALLOCATED.value],
        to_state=AssetStatus.LOST.value,
        roles=[UserRole.ADMIN]
    )
    wf.add_transition(
        name="MARK_FOUND",
        from_states=[AssetStatus.LOST.value],
        to_state=AssetStatus.AVAILABLE.value,
        roles=[UserRole.ADMIN, UserRole.ASSET_MANAGER]
    )

    return wf

asset_workflow = build_asset_workflow()
