"""Tiny state-machine helper for status fields (refunds, KYC cases, flag change requests...)."""
from __future__ import annotations

from app.core.errors import InvalidTransition


class StatusMachine:
    def __init__(self, transitions: dict[str, set[str]]):
        self.transitions = {k: frozenset(v) for k, v in transitions.items()}
        self.states = frozenset(self.transitions) | frozenset().union(*self.transitions.values())

    def can_transition(self, current: str, new: str) -> bool:
        return new in self.transitions.get(current, frozenset())

    def ensure_transition(self, current: str, new: str) -> None:
        if not self.can_transition(current, new):
            raise InvalidTransition(f"Cannot change status from '{current}' to '{new}'.")

    def is_terminal(self, state: str) -> bool:
        return not self.transitions.get(state)
