"""Feed abstractions: PerceptionFeed + Targeter protocols + shared dataclasses.

These are the stable, OS-independent contracts that every feed and targeter
must satisfy.  No third-party imports; pure standard library.

Protocol summary
----------------
PerceptionFeed
    Represents one perception channel (screenshot pixels, UIA element tree,
    OCR text map, …).  Feeds are always *optional*: ``available()`` indicates
    whether the feed can run on the current platform/environment.

Targeter
    Resolves a human-readable query ("Button Einfügen", "File > Save") to a
    ``Target`` and optionally invokes it (click-free, via the OS accessibility
    API).  A feed that supports targeting also implements ``Targeter``.

Dataclasses
-----------
FeedObservation
    One snapshot from a feed: what the feed "saw" at a moment in time.

Target
    A resolved UI element ready for clicking or invocation.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Shared dataclasses
# ---------------------------------------------------------------------------


@dataclass
class FeedObservation:
    """One snapshot from a ``PerceptionFeed``.

    Attributes:
        kind:     Feed type tag, e.g. ``"screenshot"``, ``"uia_tree"``.
        elements: Structured element list (UIA / SOM / OCR hits).
                  Each element is a plain dict with at least ``name`` and
                  ``role`` keys; additional keys depend on the feed.
        text:     Flat text content where available (e.g. document text via
                  UIA TextPattern, OCR plain text).  ``None`` when not
                  applicable.
        ts:       Unix timestamp of the observation (float, ``time.time()``).
    """

    kind: str
    elements: list[dict[str, Any]] = field(default_factory=list)
    text: str | None = None
    ts: float = field(default_factory=lambda: __import__("time").time())


@dataclass
class Target:
    """A resolved UI element ready for clicking or direct invocation.

    Attributes:
        name:        Display name of the element (from the feed).
        role:        Accessibility role string, e.g. ``"Button"``,
                     ``"MenuItem"``, ``"TabItem"``.
        rect_px:     Bounding rect in physical pixels:
                     ``(x, y, width, height)`` in the *same pixel space* as
                     ``LocalExecutor`` (i.e. physical/DPI-aware pixels
                     relative to the virtual desktop origin).
        center_norm: Normalized center position ``(nx, ny)`` in 0..1 relative
                     to the virtual desktop.  Compatible with all ``oc do``
                     coordinate inputs and the 0..1 coordinate system of the
                     module.  Use this to click the element via
                     ``LocalExecutor``.
        invokable:   ``True`` when a click-free ``Targeter.invoke()`` is
                     available (e.g. UIA InvokePattern).  ``False`` means
                     the targeter falls back to a coordinate click.
        feed:        Name of the feed that produced this target (informational).
    """

    name: str
    role: str
    rect_px: tuple[int, int, int, int]  # x, y, w, h (physical pixels, virt-desktop-relative)
    center_norm: tuple[float, float]    # 0..1 normalized against the virtual desktop
    invokable: bool = False
    feed: str = ""


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class PerceptionFeed(Protocol):
    """One perception channel.

    Implementations must be importable without their optional dependencies
    installed — ``available()`` reports whether the feed is operational.

    The ``name`` attribute uniquely identifies the feed (e.g. ``"screenshot"``,
    ``"uia_windows"``).
    """

    @property
    def name(self) -> str:
        """Short, unique feed identifier."""
        ...

    def available(self) -> bool:
        """Return ``True`` if this feed can run in the current environment.

        Called at startup by the registry to build the live feed list.
        Must not raise; catching ``ImportError`` / ``OSError`` is the
        implementation's responsibility.
        """
        ...

    def observe(self, window: str | None = None) -> FeedObservation:
        """Capture a snapshot.

        Args:
            window: Optional target-window hint (title substring).  When
                ``None``, the feed uses the default (e.g. the foreground
                window for UIA, the whole virtual desktop for screenshots).

        Returns:
            A :class:`FeedObservation` for the current state.
        """
        ...


@runtime_checkable
class Targeter(Protocol):
    """Resolves a query to a UI element and optionally invokes it.

    Targeters are typically implemented by the same class as a
    :class:`PerceptionFeed`; they are kept as a separate protocol so that
    callers can type-check for targeting capability without importing feed
    internals.
    """

    def resolve(
        self, query: str, window: str | None = None
    ) -> "Target | None":
        """Find the best element matching *query*.

        Disambiguation order: exact name > prefix > contains.  If multiple
        candidates share the same rank, the first visible (rect area > 0) is
        returned.

        Args:
            query:  Element name to search for (case-insensitive).
                    May optionally include a role hint separated by a colon,
                    e.g. ``"Einfügen:TabItem"``.
            window: Optional target-window hint (title substring).

        Returns:
            A :class:`Target` or ``None`` when nothing matches.
        """
        ...

    def invoke(self, query: str, window: str | None = None) -> bool:
        """Invoke the element matching *query* (click-free where possible).

        Tries, in order:
        1. UIA InvokePattern
        2. TogglePattern
        3. SelectionItemPattern
        4. LegacyIAccessible.DoDefaultAction

        Falls back to a coordinate click via ``LocalExecutor`` if no pattern
        is available **and** an executor was supplied at construction time.

        Args:
            query:  Same semantics as :meth:`resolve`.
            window: Optional target-window hint.

        Returns:
            ``True`` on success, ``False`` if nothing was found or the
            invocation failed.
        """
        ...
