"""A scripted, SDK-free backend for dry-runs and tests.

:class:`MockBackend` replays a predefined list of :class:`BackendResult` steps,
or -- if none is given -- performs a tiny default script (screenshot, one click,
then done). It needs no API key and no network, so the whole agent loop can be
exercised deterministically.

Pure standard library.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..actions import Action, ActionType
from ..perception import Observation
from .base import BackendResult


@dataclass
class MockBackend:
    """Replays a fixed sequence of backend results.

    Args:
        script: Ordered list of :class:`BackendResult` to return, one per call
            to :meth:`start`/:meth:`step`. When exhausted, returns
            ``BackendResult(done=True)``.
    """

    script: list[BackendResult] = field(default_factory=list)
    _index: int = 0

    @property
    def name(self) -> str:
        return "mock"

    def start(self, goal: str, observation: Observation) -> BackendResult:
        if not self.script:
            # Default tiny script: take a screenshot, click center, finish.
            self.script = [
                BackendResult(actions=[Action(ActionType.SCREENSHOT)], message=f"start: {goal}"),
                BackendResult(actions=[Action(ActionType.LEFT_CLICK, x=0.5, y=0.5)]),
                BackendResult(done=True, message="task complete"),
            ]
        return self._next()

    def step(self, observation: Observation) -> BackendResult:
        return self._next()

    def _next(self) -> BackendResult:
        if self._index >= len(self.script):
            return BackendResult(done=True, message="script exhausted")
        result = self.script[self._index]
        self._index += 1
        return result
