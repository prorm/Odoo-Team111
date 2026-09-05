from typing import List, Dict, Set, Optional
from dataclasses import dataclass, field
from app.models.enums import UserRole

@dataclass
class Transition:
    name: str
    from_states: List[str]
    to_state: str
    roles: List[UserRole]

@dataclass
class StateMachineDefinition:
    name: str
    initial_state: str
    states: Set[str] = field(default_factory=set)
    terminal_states: Set[str] = field(default_factory=set)
    transitions: Dict[str, Transition] = field(default_factory=dict)

    def add_state(self, name: str, is_initial: bool = False, is_terminal: bool = False):
        self.states.add(name)
        if is_initial:
            self.initial_state = name
        if is_terminal:
            self.terminal_states.add(name)

    def add_transition(self, name: str, from_states: List[str], to_state: str, roles: List[UserRole]):
        self.transitions[name] = Transition(
            name=name,
            from_states=from_states,
            to_state=to_state,
            roles=roles
        )

class WorkflowEngine:
    @staticmethod
    def validate_transition(
        definition: StateMachineDefinition,
        current_state: str,
        action: str,
        user_role: UserRole
    ) -> str:
        if action not in definition.transitions:
            raise ValueError(f"Invalid workflow action '{action}' for workflow '{definition.name}'")

        transition = definition.transitions[action]
        if current_state not in transition.from_states:
            raise ValueError(
                f"Cannot execute '{action}' from state '{current_state}'. Allowed from: {transition.from_states}"
            )

        if user_role not in transition.roles and UserRole.ADMIN not in transition.roles:
            raise PermissionError(
                f"Role '{user_role}' is not authorized to execute '{action}'. Allowed roles: {[r.value for r in transition.roles]}"
            )

        return transition.to_state
