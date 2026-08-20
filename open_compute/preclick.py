"""Fail-closed Win32 identity verification for coordinate mouse actions.

The guard deliberately lives above the click backend: ``WindowFromPoint`` is
the final observation before ``executor.execute``.  A child or overlay handle
is promoted to its ``GA_ROOT`` top-level window and matched against the full
``hwnd`` + ``pid`` + normalized-title identity captured by ``list_windows``.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import re
import sys
from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol

from .actions import Action, ActionType


GA_ROOT = 2

WINDOW_GUARDED_ACTIONS = frozenset(
    {
        ActionType.LEFT_CLICK,
        ActionType.RIGHT_CLICK,
        ActionType.MIDDLE_CLICK,
        ActionType.DOUBLE_CLICK,
        ActionType.TRIPLE_CLICK,
        ActionType.LEFT_CLICK_DRAG,
        ActionType.MOUSE_DOWN,
    }
)


class WindowProbe(Protocol):
    def window_at_point(self, x: int, y: int) -> dict[str, Any] | None: ...


@dataclass
class PreClickVerificationError(RuntimeError):
    """Structured, serializable fail-closed result for a blocked mouse action."""

    code: str
    reason: str
    expected_window: dict[str, Any] | None = None
    actual_window: dict[str, Any] | None = None
    point_px: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.reason)

    def to_result(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "result": "preclick_verification_failed",
            "code": self.code,
            "reason": self.reason,
        }
        if self.expected_window is not None:
            result["expected_window"] = dict(self.expected_window)
        if self.actual_window is not None:
            result["actual_window"] = dict(self.actual_window)
        if self.point_px is not None:
            result["point_px"] = list(self.point_px)
        return result


class Win32WindowProbe:
    """Thin, injectable ``WindowFromPoint``/``GetAncestor`` adapter."""

    def __init__(self, user32: Any | None = None) -> None:
        self._user32 = user32

    def window_at_point(self, x: int, y: int) -> dict[str, Any] | None:
        if self._user32 is None and sys.platform != "win32":
            return None
        user32 = self._user32 or ctypes.windll.user32
        if self._user32 is None:
            _configure_user32(user32)
        try:
            child = user32.WindowFromPoint(ctypes.wintypes.POINT(int(x), int(y)))
            child_value = int(child or 0)
            if child_value <= 0:
                return None
            root = user32.GetAncestor(child_value, GA_ROOT)
            root_value = int(root or 0)
            if root_value <= 0:
                return None

            process_id = ctypes.c_ulong(0)
            if not user32.GetWindowThreadProcessId(
                root_value, ctypes.byref(process_id)
            ):
                return None
            length = int(user32.GetWindowTextLengthW(root_value))
            if length <= 0:
                return None
            buf = ctypes.create_unicode_buffer(length + 1)
            if user32.GetWindowTextW(root_value, buf, length + 1) <= 0:
                return None
            title = buf.value.strip()
            if process_id.value <= 0 or not title:
                return None
            return {
                "hwnd": root_value,
                "pid": int(process_id.value),
                "title": title,
            }
        except (AttributeError, OSError, TypeError, ValueError):
            return None


def window_identity_from_hwnd(hwnd: int, *, user32: Any | None = None) -> dict[str, Any]:
    """Read the robust identity of an already resolved top-level window."""
    if user32 is None and sys.platform != "win32":
        raise PreClickVerificationError(
            "expected_window_unresolvable",
            "window identity is only available on Windows",
        )
    api = user32 or ctypes.windll.user32
    if user32 is None:
        _configure_user32(api)
    try:
        root = int(api.GetAncestor(int(hwnd), GA_ROOT) or int(hwnd))
        process_id = ctypes.c_ulong(0)
        if not api.GetWindowThreadProcessId(root, ctypes.byref(process_id)):
            raise OSError("GetWindowThreadProcessId failed")
        length = int(api.GetWindowTextLengthW(root))
        if length <= 0:
            raise OSError("window title is empty")
        buf = ctypes.create_unicode_buffer(length + 1)
        if api.GetWindowTextW(root, buf, length + 1) <= 0:
            raise OSError("GetWindowTextW failed")
        return _validated_identity(
            {"hwnd": root, "pid": int(process_id.value), "title": buf.value}
        )
    except PreClickVerificationError:
        raise
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise PreClickVerificationError(
            "expected_window_unresolvable",
            f"could not resolve expected top-level window identity: {exc}",
        ) from exc


def expected_identity_for_window(window: str | None) -> dict[str, Any]:
    """Resolve a named window, or the foreground window, without ambiguity."""
    from .drivers.local import list_windows
    from .window_control import WindowAmbiguousError, WindowNotFoundError, resolve_window

    windows = list_windows()
    try:
        if window is not None:
            resolved = resolve_window(windows, title=window)
        else:
            foreground = [item for item in windows if item.get("foreground")]
            if len(foreground) != 1:
                raise WindowNotFoundError(
                    "exactly one foreground top-level window is required"
                )
            resolved = foreground[0]
        return _validated_identity(resolved)
    except (ValueError, WindowNotFoundError, WindowAmbiguousError) as exc:
        raise PreClickVerificationError(
            "expected_window_unresolvable",
            f"could not resolve a unique expected window: {exc}",
        ) from exc


def coordinate_frame_from_executor(executor: Any) -> dict[str, int]:
    """Return the executor's current physical-pixel coordinate frame."""
    frame = getattr(executor, "coordinate_frame", None)
    if isinstance(frame, tuple) and len(frame) == 4:
        left, top, width, height = frame
    else:
        left, top = 0, 0
        width, height = int(executor.width), int(executor.height)
    return _validated_frame(
        {"left": left, "top": top, "width": width, "height": height}
    )


def execute_with_preclick(
    executor: Any,
    action: Action,
    *,
    expected_window: Mapping[str, Any] | None = None,
    coordinate_frame: Mapping[str, Any] | None = None,
    probe: WindowProbe | None = None,
) -> Any:
    """Verify a coordinate mouse action, then call the backend exactly once.

    Non-click actions retain the historical ``executor.execute(action)`` path.
    Guarded mouse actions require both a robust expected window identity and the
    physical frame from which their normalized coordinates were derived.  The
    latter removes the dangerous window-capture/virtual-desktop frame mix-up.
    """
    if action.type not in WINDOW_GUARDED_ACTIONS:
        return executor.execute(action)

    action_expected = action.meta.get("expected_window") if action.meta else None
    action_frame = action.meta.get("coordinate_frame") if action.meta else None
    if action_expected is None:
        action_expected = expected_window
    if action_frame is None:
        action_frame = coordinate_frame
    if action_expected is None:
        raise PreClickVerificationError(
            "expected_window_required",
            "coordinate mouse actions require expected_window with hwnd, pid, and title",
        )
    if action_frame is None:
        raise PreClickVerificationError(
            "coordinate_frame_required",
            "coordinate mouse actions require the physical capture coordinate_frame",
            expected_window=dict(action_expected),
        )

    expected = _validated_identity(action_expected)
    source_frame = _validated_frame(action_frame)
    destination_frame = coordinate_frame_from_executor(executor)
    transformed, verification_points = _transform_action(
        action, source_frame, destination_frame
    )
    window_probe = probe or Win32WindowProbe()
    for point in verification_points:
        actual = window_probe.window_at_point(*point)
        if actual is None:
            raise PreClickVerificationError(
                "window_at_point_unresolvable",
                "WindowFromPoint/GetAncestor could not resolve a top-level window",
                expected_window=expected,
                point_px=point,
            )
        try:
            actual_valid = _validated_identity(actual)
        except PreClickVerificationError as exc:
            raise PreClickVerificationError(
                "window_at_point_unresolvable",
                f"window under the target point has no robust identity: {exc.reason}",
                expected_window=expected,
                actual_window=dict(actual),
                point_px=point,
            ) from exc
        if not _identities_match(expected, actual_valid):
            raise PreClickVerificationError(
                "window_identity_mismatch",
                "top-level window under the target point does not match expected_window",
                expected_window=expected,
                actual_window=actual_valid,
                point_px=point,
            )

    # Deliberately the very next call after the final WindowFromPoint + compare.
    # For drags, the end point is checked first and the press/start point last.
    return executor.execute(transformed)


def _configure_user32(user32: Any) -> None:
    """Apply pointer-sized Win32 signatures before reading HWND values."""
    user32.WindowFromPoint.argtypes = [ctypes.wintypes.POINT]
    user32.WindowFromPoint.restype = ctypes.wintypes.HWND
    user32.GetAncestor.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT]
    user32.GetAncestor.restype = ctypes.wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [
        ctypes.wintypes.HWND,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD
    user32.GetWindowTextLengthW.argtypes = [ctypes.wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [
        ctypes.wintypes.HWND,
        ctypes.wintypes.LPWSTR,
        ctypes.c_int,
    ]
    user32.GetWindowTextW.restype = ctypes.c_int


def _validated_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        hwnd = int(value["hwnd"])
        pid = int(value["pid"])
        title = str(value["title"]).strip()
    except (KeyError, TypeError, ValueError) as exc:
        raise PreClickVerificationError(
            "expected_window_invalid",
            "expected_window must contain valid hwnd, pid, and title fields",
        ) from exc
    if hwnd <= 0 or pid <= 0 or not title:
        raise PreClickVerificationError(
            "expected_window_invalid",
            "expected_window hwnd/pid must be positive and title must be non-empty",
        )
    return {"hwnd": hwnd, "pid": pid, "title": title}


def _validated_frame(value: Mapping[str, Any]) -> dict[str, int]:
    try:
        frame = {
            "left": int(value["left"]),
            "top": int(value["top"]),
            "width": int(value["width"]),
            "height": int(value["height"]),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise PreClickVerificationError(
            "coordinate_frame_invalid",
            "coordinate_frame must contain integer left, top, width, and height",
        ) from exc
    if frame["width"] <= 0 or frame["height"] <= 0:
        raise PreClickVerificationError(
            "coordinate_frame_invalid",
            "coordinate_frame width and height must be positive",
        )
    return frame


def _transform_action(
    action: Action,
    source: Mapping[str, int],
    destination: Mapping[str, int],
) -> tuple[Action, tuple[tuple[int, int], ...]]:
    if action.x is None or action.y is None:
        raise PreClickVerificationError(
            "coordinate_frame_invalid",
            f"{action.type.value} requires an explicit coordinate for verification",
        )
    px, py = _source_point(action.x, action.y, source)
    nx, ny = _destination_norm(px, py, destination)
    end_x, end_y = action.end_x, action.end_y
    verification_points = (px, py),
    if end_x is not None and end_y is not None:
        end_px, end_py = _source_point(end_x, end_y, source)
        end_x, end_y = _destination_norm(end_px, end_py, destination)
        if action.type is ActionType.LEFT_CLICK_DRAG:
            # Verify the release/end point too, but keep the press/start point
            # as the final probe immediately before backend dispatch.
            verification_points = ((end_px, end_py), (px, py))
    return (
        replace(action, x=nx, y=ny, end_x=end_x, end_y=end_y),
        verification_points,
    )


def _source_point(
    nx: float, ny: float, frame: Mapping[str, int]
) -> tuple[int, int]:
    return (
        int(frame["left"] + round(float(nx) * max(0, frame["width"] - 1))),
        int(frame["top"] + round(float(ny) * max(0, frame["height"] - 1))),
    )


def _destination_norm(
    px: int, py: int, frame: Mapping[str, int]
) -> tuple[float, float]:
    right = frame["left"] + frame["width"] - 1
    bottom = frame["top"] + frame["height"] - 1
    if not (frame["left"] <= px <= right and frame["top"] <= py <= bottom):
        raise PreClickVerificationError(
            "coordinate_outside_executor_frame",
            "capture coordinate maps outside the executor's current desktop frame",
            point_px=(px, py),
        )
    return (
        (px - frame["left"]) / max(1, frame["width"] - 1),
        (py - frame["top"]) / max(1, frame["height"] - 1),
    )


def _identities_match(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> bool:
    return (
        int(expected["hwnd"]) == int(actual["hwnd"])
        and int(expected["pid"]) == int(actual["pid"])
        and _normalize_title(str(expected["title"]))
        == _normalize_title(str(actual["title"]))
    )


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().casefold()
