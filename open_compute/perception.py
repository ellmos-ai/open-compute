"""Perception layer: how the agent "sees" the screen.

The hybrid perception interface combines a raw screenshot with optional
semantic structure -- Set-of-Marks (e.g. OmniParser), an accessibility tree, or
a browser DOM snapshot. Providers are pluggable behind the
:class:`PerceptionProvider` protocol.

Only :class:`ScreenshotPerception` is fully implemented (it just carries the raw
bytes a driver captured). The semantic providers
(:class:`SetOfMarksProvider`, :class:`AccessibilityProvider`,
:class:`DomSnapshotProvider`) are **stubs** -- they define the interface and
return empty structure. They are clearly marked as such; wiring in a real
OmniParser / Playwright / accessibility backend is future work.

Pure standard library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class Observation:
    """A single perception result.

    Attributes:
        screenshot: Raw screenshot bytes (PNG/JPEG), or ``None`` in dry-run.
        width, height: Pixel dimensions the screenshot was captured at.
        marks: Set-of-Marks elements: list of ``{"id", "bbox", "label"}`` dicts
            where ``bbox`` is normalized ``[x0, y0, x1, y1]``. Empty if no
            semantic provider is active.
        accessibility: Accessibility/DOM tree as a nested dict, or ``None``.
    """

    screenshot: bytes | None
    width: int
    height: int
    marks: list[dict[str, Any]] = field(default_factory=list)
    accessibility: dict[str, Any] | None = None


@runtime_checkable
class PerceptionProvider(Protocol):
    """A provider that turns raw capture into an :class:`Observation`."""

    def observe(self, screenshot: bytes | None, width: int, height: int) -> Observation:
        """Return an :class:`Observation` for the given raw capture."""
        ...


@dataclass
class ScreenshotPerception:
    """Vision-only perception: pass the screenshot through unchanged.

    This is the fully implemented baseline and the right choice for the pure
    pixel-vision method (Claude / OpenAI computer use).
    """

    def observe(self, screenshot: bytes | None, width: int, height: int) -> Observation:
        return Observation(screenshot=screenshot, width=width, height=height)


@dataclass
class SetOfMarksProvider:
    """STUB: Set-of-Marks overlay (e.g. Microsoft OmniParser V2).

    A real implementation would run a screen-parser model to detect interactive
    elements and return numbered, bounding-boxed marks so the model can pick a
    mark instead of a raw pixel. This stub returns no marks.
    """

    def observe(self, screenshot: bytes | None, width: int, height: int) -> Observation:
        # STUB -- no parser wired in. Returns the screenshot with empty marks.
        return Observation(
            screenshot=screenshot,
            width=width,
            height=height,
            marks=[],
            accessibility=None,
        )


@dataclass
class AccessibilityProvider:
    """STUB: OS accessibility-tree (e.g. Windows UI Automation) perception.

    A real implementation would query the platform accessibility API for a tree
    of named, addressable elements. This stub returns an empty tree.
    """

    def observe(self, screenshot: bytes | None, width: int, height: int) -> Observation:
        # STUB -- no accessibility backend wired in.
        return Observation(
            screenshot=screenshot,
            width=width,
            height=height,
            accessibility={"role": "root", "children": []},
        )


@dataclass
class DomSnapshotProvider:
    """STUB: browser DOM / accessibility snapshot (e.g. via Playwright).

    A real implementation would capture a structured accessibility snapshot of
    the page. This stub returns an empty tree.
    """

    def observe(self, screenshot: bytes | None, width: int, height: int) -> Observation:
        # STUB -- no browser backend wired in.
        return Observation(
            screenshot=screenshot,
            width=width,
            height=height,
            accessibility={"role": "document", "children": []},
        )
