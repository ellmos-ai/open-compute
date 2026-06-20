"""The backend protocol and its result type.

A backend turns perception + history into the next canonical actions. The agent
loop owns execution and coordinate denormalization, so backends return
**canonical** actions only -- this keeps coordinate handling in one place and
makes backend dispatch trivially testable via :class:`MockBackend`.

Pure standard library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..actions import Action
from ..perception import Observation


@dataclass
class BackendResult:
    """One reasoning step's output from a backend.

    Attributes:
        actions: Canonical actions to execute next, in order.
        done: True when the backend considers the task complete.
        message: Optional human-readable assistant text for this step.
        raw: Optional backend-native payload, for debugging/inspection.
    """

    actions: list[Action] = field(default_factory=list)
    done: bool = False
    message: str | None = None
    raw: Any | None = None


@runtime_checkable
class ComputerBackend(Protocol):
    """A model backend that drives the agent loop."""

    @property
    def name(self) -> str:
        """Short backend identifier (e.g. ``mock``, ``claude``, ``openai``)."""
        ...

    def start(self, goal: str, observation: Observation) -> BackendResult:
        """Begin a task from ``goal`` and the first observation."""
        ...

    def step(self, observation: Observation) -> BackendResult:
        """Advance one step given the latest observation (e.g. a new screenshot)."""
        ...
