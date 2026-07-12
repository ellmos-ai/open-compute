"""A fully working, SDK-free executor for dry-runs and tests.

:class:`MockExecutor` records every action it is asked to perform and returns a
synthetic :class:`~open_compute.perception.Observation`. It implements the
:class:`~open_compute.drivers.base.Executor` protocol and the host-side OS
extensions, so the agent loop can run end-to-end with no real automation,
network, or display.

Pure standard library.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..actions import Action, ActionType
from ..perception import Observation


@dataclass
class MockExecutor:
    """In-memory executor that logs actions and fabricates observations.

    Args:
        width, height: Reported display dimensions.
        fake_screenshot: Bytes returned as the synthetic screenshot.
    """

    width: int = 1280
    height: int = 800
    fake_screenshot: bytes = b"PNG-MOCK"
    performed: list[Action] = field(default_factory=list)

    def execute(self, action: Action) -> Observation:
        """Record the action and return a fresh observation."""
        self.performed.append(action)
        # Host-side OS actions are accepted so the loop can exercise them.
        if action.type in (ActionType.LAUNCH_APP, ActionType.ACTIVATE_WINDOW):
            return self.screenshot()
        return self.screenshot()

    def screenshot(self) -> Observation:
        """Return a synthetic observation of the current 'screen'."""
        return Observation(
            screenshot=self.fake_screenshot,
            width=self.width,
            height=self.height,
        )

    # Host-side OS conveniences (mirrors the OSDriver surface).
    def launch_app(self, app_name: str) -> None:
        self.performed.append(Action(ActionType.LAUNCH_APP, app_name=app_name))

    def activate_window(self, app_name: str) -> None:
        self.performed.append(Action(ActionType.ACTIVATE_WINDOW, app_name=app_name))

    def release_all(self) -> dict[str, list]:
        """Mirror LocalExecutor.release_all so callers can hold it uniformly.

        The mock synthesizes no real input, so it holds nothing and there is
        nothing to release; it reports the empty state rather than raising, so
        the server's shutdown path works against any injected executor.
        """
        return {"buttons": [], "keys": []}
