"""The agent loop / orchestrator.

Implements the perception -> model-tool-call -> action -> feedback cycle:

1. Capture an :class:`~open_compute.perception.Observation` (perception).
2. Ask the backend for the next canonical actions (model tool call).
3. Gate each action through the :class:`~open_compute.safety.SafetyPolicy`.
4. Execute allowed actions via the injected :class:`~open_compute.drivers.base.Executor`.
5. Feed the resulting observation back to the backend (feedback) and repeat.

The loop owns coordinate denormalization (via the executor's pixel space) and is
backend-agnostic: backends return *canonical* actions, the executor performs
them, and the safety gate sits between. Dependency injection of the backend,
executor, perception provider, and policy makes the whole thing runnable and
testable without any SDK -- the default wiring uses the mock executor + backend.

Pure standard library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .actions import Action
from .backends.base import ComputerBackend
from .backends.mock import MockBackend
from .config import Config
from .drivers.base import Executor
from .drivers.mock import MockExecutor
from .perception import Observation, PerceptionProvider, ScreenshotPerception
from .safety import Decision, SafetyPolicy


@dataclass
class StepTrace:
    """Record of a single loop iteration, for inspection and tests."""

    index: int
    backend_message: str | None
    proposed: list[Action]
    executed: list[Action]
    denied: list[Action]


@dataclass
class LoopResult:
    """Outcome of a full :meth:`AgentLoop.run`."""

    done: bool
    steps: int
    traces: list[StepTrace] = field(default_factory=list)


@dataclass
class AgentLoop:
    """Backend-agnostic computer-use orchestrator.

    Args:
        config: The :class:`~open_compute.config.Config`.
        backend: A :class:`ComputerBackend`. Defaults to :class:`MockBackend`.
        executor: An :class:`Executor`. Defaults to a :class:`MockExecutor`
            sized to the config display.
        perception: A perception provider. Defaults to vision-only
            :class:`ScreenshotPerception`.
        policy: A :class:`SafetyPolicy`. Defaults to one built from
            ``config.safety_mode``.
    """

    config: Config
    backend: ComputerBackend | None = None
    executor: Executor | None = None
    perception: PerceptionProvider | None = None
    policy: SafetyPolicy | None = None

    def __post_init__(self) -> None:
        if self.executor is None:
            self.executor = MockExecutor(
                width=self.config.display_width,
                height=self.config.display_height,
            )
        if self.backend is None:
            self.backend = MockBackend()
        if self.perception is None:
            self.perception = ScreenshotPerception()
        if self.policy is None:
            self.policy = SafetyPolicy(mode=self.config.safety_mode)

    def run(self, goal: str) -> LoopResult:
        """Run the loop for ``goal`` until the backend is done or steps run out."""
        assert self.executor is not None
        assert self.backend is not None
        assert self.perception is not None
        assert self.policy is not None

        traces: list[StepTrace] = []
        observation = self._observe(self.executor.screenshot())
        result = self.backend.start(goal, observation)

        step = 0
        while step < self.config.max_steps:
            trace = self._handle(step, result)
            traces.append(trace)
            if result.done:
                return LoopResult(done=True, steps=step + 1, traces=traces)

            observation = self._observe(self.executor.screenshot())
            result = self.backend.step(observation)
            step += 1

        return LoopResult(done=False, steps=step, traces=traces)

    def _handle(self, index: int, result: Any) -> StepTrace:
        assert self.executor is not None
        assert self.policy is not None
        assert self.perception is not None

        executed: list[Action] = []
        denied: list[Action] = []
        for action in result.actions:
            decision = self.policy.evaluate(action).decision
            if decision is Decision.ALLOW:
                self._observe(self.executor.execute(action))
                executed.append(action)
            else:
                # CONFIRM (no callback supplied) and DENY both block execution.
                denied.append(action)
        return StepTrace(
            index=index,
            backend_message=result.message,
            proposed=list(result.actions),
            executed=executed,
            denied=denied,
        )

    def _observe(self, raw: Observation) -> Observation:
        assert self.perception is not None
        return self.perception.observe(raw.screenshot, raw.width, raw.height)
