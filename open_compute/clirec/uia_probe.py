"""Optional UIA enrichment for clirec: element under a point + password focus.

Degrades to a no-op probe when UI Automation is unavailable. The concrete
UIA adapter is loaded lazily via `_load()` (overridable in tests).
"""

from __future__ import annotations


class _UiaAdapter:
    """Thin adapter over open_compute.feeds.uia_windows / uiautomation.

    Loaded lazily so importing clirec never requires the uia extra.
    """

    def __init__(self):
        import uiautomation as auto  # raises ImportError if extra missing
        self._auto = auto

    def element_from_point(self, x: int, y: int):
        ctrl = self._auto.ControlFromPoint(x, y)
        if ctrl is None:
            return None
        top = ctrl.GetTopLevelControl()
        return {
            "name": ctrl.Name or "",
            "window": (top.Name if top else "") or "",
            "role": ctrl.ControlTypeName or "",
        }

    def focused_is_password(self) -> bool:
        ctrl = self._auto.GetFocusedControl()
        if ctrl is None:
            return False
        # Edit control with password style → IsPassword via legacy/value pattern
        return bool(getattr(ctrl, "IsPassword", False)) or \
            ctrl.ControlTypeName == "EditControl" and bool(getattr(ctrl, "IsPassword", False))


class DefaultProbe:
    def __init__(self):
        self._backend = None
        self._tried = False

    def _load(self):
        try:
            return _UiaAdapter()
        except Exception:
            return None

    def _backend_or_none(self):
        if not self._tried:
            self._backend = self._load()
            self._tried = True
        return self._backend

    def available(self) -> bool:
        return self._backend_or_none() is not None

    def element_at(self, x: int, y: int):
        be = self._backend_or_none()
        if be is None:
            return None
        try:
            return be.element_from_point(x, y)
        except Exception:
            return None

    def is_password_focused(self) -> bool:
        be = self._backend_or_none()
        if be is None:
            return False
        try:
            return bool(be.focused_is_password())
        except Exception:
            return False
