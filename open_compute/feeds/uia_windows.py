"""Windows UI Automation feed + targeter.

Implements ``PerceptionFeed`` and ``Targeter`` for Windows UIA
(UI Automation) via the ``uiautomation`` package (MIT license).

Install the optional extra to use this feed::

    pip install open-compute[uia]

This module is importable on any platform without ``uiautomation`` installed.
``available()`` returns ``False`` gracefully when the package is absent or when
not running on Windows.

Coordinate system
-----------------
UIA ``BoundingRectangle`` returns physical pixel coordinates in the same
DPI-aware frame as ``GetSystemMetrics(SM_*VIRTUALSCREEN)`` (provided the
process has called ``SetProcessDpiAwarenessContext`` before querying UIA —
``LocalExecutor.__post_init__`` does this automatically; we call it here too).

The ``Rect`` object from uiautomation has attributes: ``.left``, ``.top``,
``.right``, ``.bottom``, ``.width``, ``.height``, ``.xcenter``, ``.ycenter``.

``center_norm`` is computed as::

    nx = (rect.xcenter - virt_left) / virt_width
    ny = (rect.ycenter - virt_top)  / virt_height

where ``virt_left, virt_top, virt_width, virt_height`` come from
``GetSystemMetrics(SM_XVIRTUALSCREEN, ...)`` (same as LocalExecutor).

This is the exact inverse of ``LocalExecutor._sendinput_coords``:
    ``to_sendinput_coords(nx, ny, ...) → 0..65535``
    ``→ physical_px ≈ virt_left + nx * virt_width``

So ``center_norm`` → ``oc do left_click x=<nx> y=<ny>`` lands on the element.

Element tree limits
-------------------
To avoid freezing on large UIA trees, ``observe()`` limits depth and count.
Configure via constructor or environment variables:

    ``OC_UIA_MAX_DEPTH``  (int, default 8)
    ``OC_UIA_MAX_ELEM``   (int, default 200)

Invoke fallback order
---------------------
1. InvokePattern.Invoke()
2. TogglePattern.Toggle()
3. SelectionItemPattern.Select()
4. LegacyIAccessible.DoDefaultAction()
"""

from __future__ import annotations

import os
import re
import sys
import time
from typing import Any

from .base import FeedObservation, Target

# ---------------------------------------------------------------------------
# Module-level constants (no uiautomation imported here)
# ---------------------------------------------------------------------------

_DEFAULT_MAX_DEPTH: int = 8
_DEFAULT_MAX_ELEM: int = 200


# ---------------------------------------------------------------------------
# Lazy UIA accessor
# ---------------------------------------------------------------------------

def _get_uia():
    """Return the ``uiautomation`` module, importing it lazily.

    Raises ``ImportError`` when the package is not installed.
    All UIA operations MUST go through this accessor so that the import
    never happens at module load time (zero-runtime-deps guarantee).
    """
    import uiautomation as _uia  # noqa: PLC0415 — intentional lazy import
    return _uia


# ---------------------------------------------------------------------------
# Coordinate helpers
# ---------------------------------------------------------------------------

def _get_virtual_desktop() -> tuple[int, int, int, int]:
    """Return (left, top, width, height) of the virtual desktop in pixels.

    Mirrors ``open_compute.drivers.local._get_virtual_desktop`` so that
    center_norm values are identical regardless of which module computes them.
    """
    import ctypes
    gm = ctypes.windll.user32.GetSystemMetrics
    SM_XVIRTUALSCREEN = 76
    SM_YVIRTUALSCREEN = 77
    SM_CXVIRTUALSCREEN = 78
    SM_CYVIRTUALSCREEN = 79
    left = gm(SM_XVIRTUALSCREEN)
    top = gm(SM_YVIRTUALSCREEN)
    width = gm(SM_CXVIRTUALSCREEN)
    height = gm(SM_CYVIRTUALSCREEN)
    if width <= 0:
        width = gm(0)
        height = gm(1)
        left = 0
        top = 0
    return left, top, width, height


def _set_dpi_awareness() -> None:
    """Best-effort: Per-Monitor-v2 DPI awareness so UIA rects match mss pixels."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        fn = ctypes.windll.user32.SetProcessDpiAwarenessContext
        fn.restype = ctypes.c_bool
        fn.argtypes = [ctypes.c_void_p]
        fn(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass


def _rect_to_center_norm(
    rect,  # uiautomation Rect OR a mock with .x/.y/.width/.height OR .xcenter/.ycenter
    virt_left: int,
    virt_top: int,
    virt_width: int,
    virt_height: int,
) -> tuple[float, float]:
    """Map a UIA BoundingRectangle to a normalized (0..1) center coordinate.

    Handles two rect conventions:

    1. **Real uiautomation Rect** — has ``.left``, ``.top``, ``.right``,
       ``.bottom`` as int attributes and ``.xcenter()``, ``.ycenter()`` as
       methods.  (``width`` and ``height`` are also methods, NOT properties.)
    2. **Test mocks** — expose ``.x``, ``.y``, ``.width``, ``.height`` as
       plain int attributes (or ``.xcenter``/``.ycenter`` as plain floats).

    Formula (matches LocalExecutor._sendinput_coords inverse):
        nx = (cx - virt_left) / virt_width
        ny = (cy - virt_top)  / virt_height
    """
    if virt_width <= 0 or virt_height <= 0:
        return 0.0, 0.0

    cx: float
    cy: float

    # Prefer xcenter/ycenter attributes; distinguish methods vs plain attrs.
    if hasattr(rect, "xcenter"):
        xc = rect.xcenter
        yc = rect.ycenter
        # Real uiautomation: xcenter / ycenter are METHODS, not properties
        if callable(xc):
            cx = float(xc())
            cy = float(yc())
        else:
            cx = float(xc)
            cy = float(yc)
    elif hasattr(rect, "left"):
        # Real Rect without xcenter exposed (older uiautomation) — use corners
        cx = (float(rect.left) + float(rect.right)) / 2.0
        cy = (float(rect.top) + float(rect.bottom)) / 2.0
    else:
        # Test mock: .x / .y / .width / .height are plain numbers
        cx = float(rect.x) + float(rect.width) / 2.0
        cy = float(rect.y) + float(rect.height) / 2.0

    nx = (cx - virt_left) / virt_width
    ny = (cy - virt_top) / virt_height
    nx = max(0.0, min(1.0, nx))
    ny = max(0.0, min(1.0, ny))
    return nx, ny


def _rect_to_px_tuple(rect) -> tuple[int, int, int, int]:
    """Convert a UIA BoundingRectangle to (x, y, w, h) int tuple.

    Handles both real uiautomation Rect objects and test mocks:
    - Real Rect: ``.left``, ``.top``, ``.right``, ``.bottom`` are ints;
      ``.width`` and ``.height`` are METHODS (must be called).
    - Test mocks: ``.x``, ``.y``, ``.width``, ``.height`` as plain ints.
    """
    if hasattr(rect, "left"):
        # Real uiautomation Rect — derive width/height from corners
        x = int(rect.left)
        y = int(rect.top)
        w = int(rect.right) - x
        h = int(rect.bottom) - y
        return (x, y, w, h)
    # Test mock
    return (int(rect.x), int(rect.y), int(rect.width), int(rect.height))


# ---------------------------------------------------------------------------
# Element traversal via uiautomation.WalkControl
# ---------------------------------------------------------------------------

def _is_visible_rect(rect) -> bool:
    """Return True when the rect has non-zero dimensions.

    Handles both real uiautomation Rect (where ``.width`` is a **method**)
    and test mocks (where ``.width`` is a plain int attribute).
    """
    try:
        if hasattr(rect, "left"):
            # Real uiautomation Rect — use corners (always int attributes)
            w = int(rect.right) - int(rect.left)
            h = int(rect.bottom) - int(rect.top)
        else:
            # Test mock or dict-like
            w = int(rect.width)
            h = int(rect.height)
        return w > 0 and h > 0
    except Exception:  # noqa: BLE001
        return False


def _walk_tree(
    root_control,
    max_depth: int,
    max_elem: int,
) -> list[dict[str, Any]]:
    """Walk the UIA ControlView tree using uiautomation.WalkControl().

    Returns a flat list of element dicts with keys:
        name, role, value, rect_px (x,y,w,h), visible, depth
    """
    uia = _get_uia()
    results: list[dict[str, Any]] = []

    try:
        for ctrl, depth in uia.WalkControl(root_control, maxDepth=max_depth):
            if len(results) >= max_elem:
                break
            try:
                rect = ctrl.BoundingRectangle
                results.append({
                    "name": ctrl.Name or "",
                    "role": ctrl.ControlTypeName or "",
                    "value": _safe_value(ctrl),
                    "rect_px": _rect_to_px_tuple(rect),
                    "visible": _is_visible_rect(rect),
                    "depth": depth,
                })
            except Exception:  # noqa: BLE001 — UIA can raise COM errors
                continue
    except Exception:  # noqa: BLE001
        pass

    return results


def _safe_value(ctrl) -> str:
    """Attempt to read Value from ValuePattern; return '' on failure."""
    try:
        vp = ctrl.GetValuePattern()
        if vp:
            return vp.Value or ""
    except Exception:  # noqa: BLE001
        pass
    return ""


# ---------------------------------------------------------------------------
# Disambiguator
# ---------------------------------------------------------------------------

def _disambiguate(
    name_query: str,
    role_query: str | None,
    elements: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Pick the best element for (name_query, role_query).

    Priority: exact name > prefix > contains.
    Within a tier, prefers visible elements (rect area > 0).
    Returns the first match in the best tier, or ``None``.
    """
    q = name_query.lower()

    def _matches_role(elem: dict[str, Any]) -> bool:
        if role_query is None:
            return True
        return role_query.lower() in elem["role"].lower()

    candidates = [e for e in elements if _matches_role(e)]

    # Tier 1: exact
    tier = [e for e in candidates if e["name"].lower() == q]
    if tier:
        visible = [e for e in tier if e["visible"]]
        return (visible or tier)[0]

    # Tier 2: prefix
    tier = [e for e in candidates if e["name"].lower().startswith(q)]
    if tier:
        visible = [e for e in tier if e["visible"]]
        return (visible or tier)[0]

    # Tier 3: contains
    tier = [e for e in candidates if q in e["name"].lower()]
    if tier:
        visible = [e for e in tier if e["visible"]]
        return (visible or tier)[0]

    return None


# ---------------------------------------------------------------------------
# Live control finder (for invoke — needs the actual UIA object, not dict)
# ---------------------------------------------------------------------------

def _find_live_control(root_control, name_query: str, role_query: str | None, max_depth: int):
    """Walk the tree and return the first live UIA control matching the query.

    Used by ``invoke()`` to hold a real UIA control reference on which
    patterns can be called.  Returns ``None`` if nothing matches.
    """
    uia = _get_uia()
    q = name_query.lower()

    best: list[tuple[int, Any]] = []  # (tier, ctrl)

    try:
        for ctrl, depth in uia.WalkControl(root_control, maxDepth=max_depth):
            try:
                name = (ctrl.Name or "").lower()
                role = ctrl.ControlTypeName or ""
                matches_role = role_query is None or role_query.lower() in role.lower()
                if not matches_role:
                    continue
                if name == q:
                    best.append((0, ctrl))
                elif name.startswith(q):
                    best.append((1, ctrl))
                elif q in name:
                    best.append((2, ctrl))
                # Stop early once we have an exact match
                if best and best[0][0] == 0:
                    break
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass

    if not best:
        return None
    best.sort(key=lambda t: t[0])
    return best[0][1]


# ---------------------------------------------------------------------------
# Invoke fallback chain
# ---------------------------------------------------------------------------

def _invoke_control(ctrl) -> bool:
    """Invoke a UIA control via the pattern fallback chain.

    Tries:
    1. InvokePattern.Invoke()
    2. TogglePattern.Toggle()
    3. SelectionItemPattern.Select()
    4. LegacyIAccessible.DoDefaultAction()

    Returns True on success, False if no applicable pattern was found.
    """
    # 1. InvokePattern
    try:
        ip = ctrl.GetInvokePattern()
        if ip is not None:
            ip.Invoke()
            return True
    except Exception:  # noqa: BLE001
        pass

    # 2. TogglePattern
    try:
        tp = ctrl.GetTogglePattern()
        if tp is not None:
            tp.Toggle()
            return True
    except Exception:  # noqa: BLE001
        pass

    # 3. SelectionItemPattern
    try:
        sp = ctrl.GetSelectionItemPattern()
        if sp is not None:
            sp.Select()
            return True
    except Exception:  # noqa: BLE001
        pass

    # 4. LegacyIAccessible.DoDefaultAction
    try:
        la = ctrl.GetLegacyIAccessiblePattern()
        if la is not None:
            la.DoDefaultAction()
            return True
    except Exception:  # noqa: BLE001
        pass

    return False


# ---------------------------------------------------------------------------
# Window-name normalization helper
# ---------------------------------------------------------------------------

def _normalize_window_name(s: str) -> str:
    """Collapse runs of whitespace (including tabs) to a single space and strip.

    Windows application titles often contain multiple consecutive spaces, e.g.
    ``"Schnitzeljagd  -  Kompatibilitätsmodus - Word"`` (Doppel-Leerzeichen).
    Normalizing both the query and the candidate title makes substring matches
    robust against such cosmetic differences.
    """
    return re.sub(r"\s+", " ", s).strip()


# ---------------------------------------------------------------------------
# Root window helper
# ---------------------------------------------------------------------------

def _get_foreground_hwnd() -> int | None:
    """Return the HWND of the current foreground window, or None on failure."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        return int(hwnd) if hwnd else None
    except Exception:  # noqa: BLE001
        return None


def _get_root(window: str | None):
    """Return the UIA root control for the target window.

    If ``window`` is None or empty, resolves the foreground window:
    1. ``GetForegroundWindow()`` (Win32) → ``FromHandle(hwnd)`` via uiautomation.
    2. Falls back to ``GetForegroundControl()`` if FromHandle is unavailable.
    3. Ultimate fallback: ``GetRootControl()`` (desktop root).

    If ``window`` is a non-empty string, searches top-level windows (children
    of the UIA desktop root) for one whose title *contains* the query as a
    case-insensitive substring.  Both the window title and the query are
    whitespace-normalized first (multiple spaces/tabs collapsed to one space)
    to handle Windows titles like ``"Doc  -  Word"`` (double spaces).

    Raises:
        RuntimeError: When ``window`` is specified but no matching top-level
            window is found.  The error message includes the requested name so
            callers can surface it to the user without a silent fallback.
    """
    uia = _get_uia()

    if not window:
        # Prefer the explicit HWND → FromHandle path so tests can mock
        # GetForegroundWindow at the Win32 level.
        hwnd = _get_foreground_hwnd()
        if hwnd is not None:
            try:
                from_handle = getattr(uia, "ControlFromHandle", None) or getattr(uia, "AutomationElementFromHandle", None)
                if from_handle is not None:
                    ctrl = from_handle(hwnd)
                    if ctrl is not None:
                        return ctrl
            except Exception:  # noqa: BLE001
                pass

        # Fallback 1: GetForegroundControl
        try:
            ctrl = uia.GetForegroundControl()
            if ctrl is not None:
                return ctrl
        except Exception:  # noqa: BLE001
            pass

        # Fallback 2: desktop root (last resort for the no-window case)
        return uia.GetRootControl()

    # --- Named window resolution ---
    # Normalize query once; compare against normalized candidate titles.
    q_norm = _normalize_window_name(window).lower()

    try:
        root = uia.GetRootControl()
        child = root.GetFirstChildControl()
        while child is not None:
            try:
                title_norm = _normalize_window_name(child.Name or "").lower()
                if q_norm in title_norm:
                    return child
            except Exception:  # noqa: BLE001
                pass
            try:
                child = child.GetNextSiblingControl()
            except Exception:  # noqa: BLE001
                break
    except Exception:  # noqa: BLE001
        pass

    # No window found by name — raise instead of silently falling back to
    # the desktop root (which would scope searches to the Taskbar / entire
    # desktop, producing wrong results without any warning).
    raise RuntimeError(
        f"No top-level window found matching {window!r}. "
        "Check the window title substring or omit --window to use the "
        "foreground window."
    )


# ---------------------------------------------------------------------------
# UiaWindowsFeed
# ---------------------------------------------------------------------------

class UiaWindowsFeed:
    """Windows UIA perception feed + targeter.

    Implements ``PerceptionFeed`` and ``Targeter`` protocols.

    Install with: ``pip install open-compute[uia]``

    Args:
        max_depth: Maximum UIA tree traversal depth (default: 8 or
            ``OC_UIA_MAX_DEPTH`` env var).
        max_elem: Maximum number of elements to collect (default: 200 or
            ``OC_UIA_MAX_ELEM`` env var).
    """

    name: str = "uia_windows"

    def __init__(
        self,
        max_depth: int | None = None,
        max_elem: int | None = None,
    ) -> None:
        self._max_depth = max_depth or int(
            os.environ.get("OC_UIA_MAX_DEPTH", str(_DEFAULT_MAX_DEPTH))
        )
        self._max_elem = max_elem or int(
            os.environ.get("OC_UIA_MAX_ELEM", str(_DEFAULT_MAX_ELEM))
        )

    def available(self) -> bool:
        """Return True on Windows with ``uiautomation`` importable."""
        if sys.platform != "win32":
            return False
        try:
            _get_uia()
            return True
        except Exception:  # noqa: BLE001 — import may raise OSError/COM errors, not just ImportError
            return False

    # ------------------------------------------------------------------
    # PerceptionFeed
    # ------------------------------------------------------------------

    def observe(self, window: str | None = None) -> FeedObservation:
        """Walk the UIA element tree and return a ``FeedObservation``.

        Also attempts to read document text via TextPattern (if the root
        control exposes one).

        Args:
            window: Optional title substring to select the target window.
                ``None`` = foreground window.

        Raises:
            ImportError: if ``uiautomation`` is not installed
                (``pip install open-compute[uia]``).
            RuntimeError: on non-Windows platforms, or when ``window`` is
                specified but no matching top-level window is found.
        """
        if sys.platform != "win32":
            raise RuntimeError(
                "UiaWindowsFeed is Windows-only. "
                "Install open-compute[uia] on Windows."
            )

        _set_dpi_awareness()
        # _get_root raises RuntimeError when window is specified but not found.
        # Let that propagate — the caller must handle a missing window explicitly.
        root = _get_root(window)
        if root is None:
            return FeedObservation(kind="uia_tree", elements=[], text=None, ts=time.time())

        elements = _walk_tree(root, self._max_depth, self._max_elem)

        # Attempt to read document text via TextPattern
        doc_text: str | None = None
        try:
            uia = _get_uia()
            tp = root.GetTextPattern()
            if tp:
                doc_text = tp.DocumentRange.GetText(-1)
        except Exception:  # noqa: BLE001
            pass

        return FeedObservation(
            kind="uia_tree",
            elements=elements,
            text=doc_text,
            ts=time.time(),
        )

    # ------------------------------------------------------------------
    # Targeter
    # ------------------------------------------------------------------

    def resolve(self, query: str, window: str | None = None) -> Target | None:
        """Resolve a name query to a ``Target`` via the UIA element tree.

        Query syntax::

            "name"          — match by name (case-insensitive)
            "name:Role"     — match by name restricted to given role

        Args:
            query:  Name (and optional role) to search for.
            window: Optional window title substring.

        Returns:
            A :class:`Target` or ``None`` when no element matches.
        """
        if sys.platform != "win32":
            return None
        if not self.available():
            return None

        # Parse optional role hint from "name:Role"
        name_q, _, role_q = query.partition(":")
        name_q = name_q.strip()
        role_q = role_q.strip() or None

        _set_dpi_awareness()
        obs = self.observe(window=window)

        best = _disambiguate(name_q, role_q, obs.elements)
        if best is None:
            return None

        virt_left, virt_top, virt_width, virt_height = _get_virtual_desktop()
        rx, ry, rw, rh = best["rect_px"]

        # Build a fake rect-like object for _rect_to_center_norm (x/y/width/height)
        class _Rect:
            def __init__(self, x, y, w, h):
                self.x, self.y, self.width, self.height = x, y, w, h

        rect = _Rect(rx, ry, rw, rh)
        center_norm = _rect_to_center_norm(rect, virt_left, virt_top, virt_width, virt_height)

        # Check for InvokePattern on the live control (best-effort)
        invokable = self._check_invokable(name_q, role_q, window)

        return Target(
            name=best["name"],
            role=best["role"],
            rect_px=(rx, ry, rw, rh),
            center_norm=center_norm,
            invokable=invokable,
            feed=self.name,
        )

    def invoke(self, query: str, window: str | None = None) -> bool:
        """Click-free invocation of the element matching *query*.

        Tries UIA patterns in fallback order (see module docstring).

        Args:
            query:  Name (and optional role) to search for.
            window: Optional window title substring.

        Returns:
            ``True`` on success, ``False`` otherwise.
        """
        if sys.platform != "win32":
            return False
        if not self.available():
            return False

        name_q, _, role_q = query.partition(":")
        name_q = name_q.strip()
        role_q = role_q.strip() or None

        _set_dpi_awareness()
        root = _get_root(window)
        if root is None:
            return False

        ctrl = _find_live_control(root, name_q, role_q, self._max_depth)
        if ctrl is None:
            return False

        return _invoke_control(ctrl)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_invokable(
        self,
        name_q: str,
        role_q: str | None,
        window: str | None,
    ) -> bool:
        """Return True if the live UIA control supports InvokePattern.

        Best-effort: walks the tree once more to find the control and
        checks for InvokePattern.  Returns False on any failure.
        """
        try:
            root = _get_root(window)
            if root is None:
                return False
            ctrl = _find_live_control(root, name_q, role_q, self._max_depth)
            if ctrl is None:
                return False
            ip = ctrl.GetInvokePattern()
            return ip is not None
        except Exception:  # noqa: BLE001
            return False
