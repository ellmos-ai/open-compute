"""Tests for the agent loop / orchestrator (dry-run with mocks)."""

from __future__ import annotations

from open_compute.actions import Action, ActionType
from open_compute.backends.base import BackendResult
from open_compute.backends.mock import MockBackend
from open_compute.config import Config
from open_compute.drivers.mock import MockExecutor
from open_compute.loop import AgentLoop
from open_compute.safety import SafetyPolicy


def test_dry_run_completes_with_defaults():
    loop = AgentLoop(Config(backend="mock", safety_mode="allow_all"))
    result = loop.run("open settings")
    assert result.done is True
    assert result.steps >= 1


def test_executed_actions_recorded_on_mock_executor():
    executor = MockExecutor(width=1280, height=800)
    backend = MockBackend(
        script=[
            BackendResult(actions=[Action(ActionType.LEFT_CLICK, x=0.5, y=0.5)]),
            BackendResult(done=True),
        ]
    )
    loop = AgentLoop(
        Config(backend="mock", safety_mode="allow_all"),
        backend=backend,
        executor=executor,
    )
    result = loop.run("click")
    # The click plus the per-step screenshots are recorded.
    click_actions = [a for a in executor.performed if a.type is ActionType.LEFT_CLICK]
    assert len(click_actions) == 1
    assert result.done is True


def test_confirm_mode_blocks_risky_actions():
    executor = MockExecutor()
    backend = MockBackend(
        script=[
            BackendResult(actions=[Action(ActionType.TYPE, text="secret")]),
            BackendResult(done=True),
        ]
    )
    loop = AgentLoop(
        Config(backend="mock", safety_mode="confirm"),
        backend=backend,
        executor=executor,
        policy=SafetyPolicy(mode="confirm"),  # no callback -> CONFIRM blocks
    )
    result = loop.run("type secret")
    typed = [a for a in executor.performed if a.type is ActionType.TYPE]
    assert typed == []  # blocked
    assert any(t.denied for t in result.traces)


def test_max_steps_caps_runaway_loop():
    # A backend that never signals done.
    class _Never(MockBackend):
        def step(self, observation):
            return BackendResult(actions=[Action(ActionType.SCREENSHOT)])

        def start(self, goal, observation):
            return BackendResult(actions=[Action(ActionType.SCREENSHOT)])

    loop = AgentLoop(
        Config(backend="mock", safety_mode="allow_all", max_steps=3),
        backend=_Never(),
    )
    result = loop.run("loop forever")
    assert result.done is False
    assert result.steps == 3
