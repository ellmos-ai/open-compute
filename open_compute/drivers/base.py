"""Driver and executor interfaces (Protocols).

These define the contract the agent loop depends on, decoupling it from any
concrete automation backend. The OS-vs-browser scope split lives here: a
:class:`BrowserDriver` constrains actions to a browser engine (e.g. Playwright),
while an :class:`OSDriver` reaches the whole desktop.

All concrete drivers other than :class:`~open_compute.drivers.mock.MockExecutor`
are out of scope for this initial release and ship as interfaces only.

Pure standard library.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..actions import Action
from ..perception import Observation


@runtime_checkable
class Executor(Protocol):
    """Executes canonical actions and reports back what the screen looks like."""

    @property
    def width(self) -> int:
        """Current display width in pixels."""
        ...

    @property
    def height(self) -> int:
        """Current display height in pixels."""
        ...

    def execute(self, action: Action) -> Observation:
        """Perform ``action`` and return a fresh :class:`Observation`."""
        ...

    def screenshot(self) -> Observation:
        """Capture the current screen without performing any action."""
        ...


@runtime_checkable
class BrowserDriver(Protocol):
    """Browser-scope driver interface (e.g. backed by Playwright / CDP).

    INTERFACE ONLY in this release -- no concrete browser driver is shipped.
    A real implementation would launch a browser context, navigate, and map
    canonical actions onto DOM/CDP operations.
    """

    def goto(self, url: str) -> None:
        """Navigate to ``url``."""
        ...

    def execute(self, action: Action) -> Observation:
        """Perform a canonical action within the browser."""
        ...

    def close(self) -> None:
        """Tear down the browser context."""
        ...


@runtime_checkable
class OSDriver(Protocol):
    """OS-scope driver interface (whole desktop).

    INTERFACE ONLY in this release. A real implementation would own the host
    input/screen and additionally implement the host-side OS actions
    (``launch_app`` / ``activate_window``) that the model tools do not provide.
    """

    def execute(self, action: Action) -> Observation:
        """Perform a canonical action against the desktop."""
        ...

    def launch_app(self, app_name: str) -> None:
        """Launch an application by name (host-side, no model-tool pendant)."""
        ...

    def activate_window(self, app_name: str) -> None:
        """Bring an application's window to the foreground."""
        ...
