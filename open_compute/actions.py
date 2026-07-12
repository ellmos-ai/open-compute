"""Canonical, backend-agnostic action schema.

This module is pure (standard library only) and never imports a vendor SDK.
It defines the smallest common set of GUI actions shared by the Claude and
OpenAI computer-use tools, plus OS-level extensions that the model tools do not
provide and that the host must execute (``launch_app`` / ``activate_window``).

Coordinates inside an :class:`Action` are stored as **normalized** floats in the
range ``0.0 .. 1.0`` (see :mod:`open_compute.coordinates`). The mapper functions
denormalize to backend-native pixel coordinates only at dispatch time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ActionType(str, Enum):
    """The canonical action vocabulary.

    The first block mirrors the intersection of the Claude ``computer`` tool
    actions and the OpenAI computer-use action set. The second block holds
    host-side extensions with no native model-tool equivalent: the OS actions
    (``LAUNCH_APP`` / ``ACTIVATE_WINDOW``) and the *hold primitives*
    (``MOUSE_DOWN`` / ``MOUSE_UP`` / ``KEY_DOWN`` / ``KEY_UP``), which decompose
    a click or key press into its press and release halves. Holds are what
    press-and-drag, rubber-band selection, modifier-held clicking and
    press-to-move game input need; the composite actions above cannot express a
    button that stays down across several other actions.
    """

    SCREENSHOT = "screenshot"
    MOUSE_MOVE = "mouse_move"
    LEFT_CLICK = "left_click"
    RIGHT_CLICK = "right_click"
    MIDDLE_CLICK = "middle_click"
    DOUBLE_CLICK = "double_click"
    TRIPLE_CLICK = "triple_click"
    LEFT_CLICK_DRAG = "left_click_drag"
    TYPE = "type"
    KEY = "key"
    SCROLL = "scroll"
    WAIT = "wait"
    CURSOR_POSITION = "cursor_position"
    # Host-side OS extensions (no native model-tool action):
    LAUNCH_APP = "launch_app"
    ACTIVATE_WINDOW = "activate_window"
    # Host-side hold primitives (no native model-tool action):
    MOUSE_DOWN = "mouse_down"
    MOUSE_UP = "mouse_up"
    KEY_DOWN = "key_down"
    KEY_UP = "key_up"


#: Hold primitives: press/release halves that leave the host in a held state.
HOLD_ACTIONS = frozenset(
    {
        ActionType.MOUSE_DOWN,
        ActionType.MOUSE_UP,
        ActionType.KEY_DOWN,
        ActionType.KEY_UP,
    }
)

#: Mouse buttons accepted by the hold primitives.
BUTTONS = ("left", "right", "middle")


# Actions that carry a single point (normalized x/y in 0..1).
_POINT_ACTIONS = frozenset(
    {
        ActionType.MOUSE_MOVE,
        ActionType.LEFT_CLICK,
        ActionType.RIGHT_CLICK,
        ActionType.MIDDLE_CLICK,
        ActionType.DOUBLE_CLICK,
        ActionType.TRIPLE_CLICK,
        ActionType.LEFT_CLICK_DRAG,
        ActionType.SCROLL,
    }
)


@dataclass
class Action:
    """A single canonical action.

    Attributes:
        type: The :class:`ActionType`.
        x, y: Normalized start coordinate (0..1), if the action targets a point.
        end_x, end_y: Normalized end coordinate for drag actions.
        text: Text to type, the key combination for ``KEY``, or the key(s) to
            press/release for ``KEY_DOWN`` / ``KEY_UP``.
        scroll_direction: One of ``up``/``down``/``left``/``right``.
        scroll_amount: Integer scroll magnitude (clicks/notches).
        duration: Seconds to wait, for ``WAIT``.
        app_name: Target application, for host-side OS actions.
        button: Mouse button for ``MOUSE_DOWN`` / ``MOUSE_UP`` (default
            ``left``). One of :data:`BUTTONS`.
        meta: Free-form extra metadata; never sent to a backend by the mappers.
    """

    type: ActionType
    x: float | None = None
    y: float | None = None
    end_x: float | None = None
    end_y: float | None = None
    text: str | None = None
    scroll_direction: str | None = None
    scroll_amount: int | None = None
    duration: float | None = None
    app_name: str | None = None
    button: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.type, ActionType):
            self.type = ActionType(self.type)
        if self.type in _POINT_ACTIONS and (self.x is None or self.y is None):
            raise ValueError(f"action {self.type.value!r} requires x and y")
        if self.type is ActionType.TYPE and self.text is None:
            raise ValueError("type action requires text")
        if self.type is ActionType.KEY and self.text is None:
            raise ValueError("key action requires text (the key combination)")
        if self.type in (ActionType.KEY_DOWN, ActionType.KEY_UP) and self.text is None:
            raise ValueError(f"{self.type.value} action requires text (the key to hold)")
        if self.button is not None and self.button not in BUTTONS:
            raise ValueError(f"button={self.button!r} must be one of {BUTTONS}")
        for name in ("x", "y", "end_x", "end_y"):
            value = getattr(self, name)
            if value is not None and not (0.0 <= float(value) <= 1.0):
                raise ValueError(
                    f"{name}={value!r} out of normalized range 0.0..1.0"
                )

    @property
    def is_host_side(self) -> bool:
        """True for actions the host executes outside the model tool.

        Covers the OS extensions and the hold primitives: neither the Claude nor
        the OpenAI computer tool has an action for them, so the mappers refuse
        them and the host driver executes them directly.
        """
        return (
            self.type in (ActionType.LAUNCH_APP, ActionType.ACTIVATE_WINDOW)
            or self.type in HOLD_ACTIONS
        )


def to_claude(action: Action, width: int, height: int) -> dict[str, Any]:
    """Map a canonical action to a Claude ``computer`` tool action dict.

    Claude uses global screen pixel coordinates against ``display_width_px`` x
    ``display_height_px`` and a ``coordinate: [x, y]`` array. Host-side OS
    actions have no Claude pendant and raise :class:`ValueError`.

    Args:
        action: The canonical action.
        width: Display width in pixels (denormalization basis).
        height: Display height in pixels.

    Returns:
        A JSON-serializable dict matching the Claude action schema.
    """
    from .coordinates import denormalize  # local import keeps module import-light

    t = action.type
    if action.is_host_side:
        raise ValueError(
            f"{t.value!r} has no Claude computer-tool equivalent; "
            "execute it host-side via the OSDriver / bash tool"
        )

    if t is ActionType.SCREENSHOT:
        return {"action": "screenshot"}
    if t is ActionType.WAIT:
        return {"action": "wait", "duration": action.duration or 1}
    if t is ActionType.CURSOR_POSITION:
        return {"action": "cursor_position"}
    if t is ActionType.TYPE:
        return {"action": "type", "text": action.text}
    if t is ActionType.KEY:
        return {"action": "key", "text": action.text}

    if t in _POINT_ACTIONS:
        px, py = denormalize(action.x, action.y, width, height)
        out: dict[str, Any] = {"action": t.value, "coordinate": [px, py]}
        if t is ActionType.LEFT_CLICK_DRAG:
            ex, ey = denormalize(
                action.end_x if action.end_x is not None else action.x,
                action.end_y if action.end_y is not None else action.y,
                width,
                height,
            )
            out["start_coordinate"] = [px, py]
            out["coordinate"] = [ex, ey]
        if t is ActionType.SCROLL:
            out["scroll_direction"] = action.scroll_direction or "down"
            out["scroll_amount"] = action.scroll_amount or 3
        return out

    raise ValueError(f"unmapped action type: {t!r}")


def to_openai(action: Action, width: int, height: int) -> dict[str, Any]:
    """Map a canonical action to an OpenAI computer-use action dict.

    The OpenAI computer-use tool (model ``computer-use-preview`` -- treated as
    configurable/unverified; see the backend) uses flat ``x``/``y`` pixel
    fields and slightly different action names (``click`` with a ``button``,
    ``keypress`` with a ``keys`` list). Host-side OS actions raise.

    Args:
        action: The canonical action.
        width: Display width in pixels.
        height: Display height in pixels.

    Returns:
        A JSON-serializable dict approximating the OpenAI action schema.
    """
    from .coordinates import denormalize

    t = action.type
    if action.is_host_side:
        raise ValueError(
            f"{t.value!r} has no OpenAI computer-tool equivalent; "
            "execute it host-side via the OSDriver"
        )

    if t is ActionType.SCREENSHOT:
        return {"type": "screenshot"}
    if t is ActionType.WAIT:
        return {"type": "wait"}
    if t is ActionType.CURSOR_POSITION:
        return {"type": "move", **_xy(action, width, height)}
    if t is ActionType.TYPE:
        return {"type": "type", "text": action.text}
    if t is ActionType.KEY:
        return {"type": "keypress", "keys": _split_keys(action.text or "")}
    if t is ActionType.MOUSE_MOVE:
        return {"type": "move", **_xy(action, width, height)}
    if t in (ActionType.LEFT_CLICK, ActionType.RIGHT_CLICK, ActionType.MIDDLE_CLICK):
        button = {"left_click": "left", "right_click": "right", "middle_click": "middle"}[t.value]
        return {"type": "click", "button": button, **_xy(action, width, height)}
    if t in (ActionType.DOUBLE_CLICK, ActionType.TRIPLE_CLICK):
        return {"type": "double_click", **_xy(action, width, height)}
    if t is ActionType.SCROLL:
        sx, sy = _scroll_delta(action)
        return {"type": "scroll", "scroll_x": sx, "scroll_y": sy, **_xy(action, width, height)}
    if t is ActionType.LEFT_CLICK_DRAG:
        start = denormalize(action.x, action.y, width, height)
        end = denormalize(
            action.end_x if action.end_x is not None else action.x,
            action.end_y if action.end_y is not None else action.y,
            width,
            height,
        )
        return {
            "type": "drag",
            "path": [
                {"x": start[0], "y": start[1]},
                {"x": end[0], "y": end[1]},
            ],
        }

    raise ValueError(f"unmapped action type: {t!r}")


def _xy(action: Action, width: int, height: int) -> dict[str, int]:
    from .coordinates import denormalize

    px, py = denormalize(action.x, action.y, width, height)
    return {"x": px, "y": py}


def _scroll_delta(action: Action) -> tuple[int, int]:
    amount = action.scroll_amount or 3
    direction = action.scroll_direction or "down"
    if direction == "up":
        return 0, -amount
    if direction == "down":
        return 0, amount
    if direction == "left":
        return -amount, 0
    if direction == "right":
        return amount, 0
    return 0, amount


def _split_keys(combo: str) -> list[str]:
    """Split a ``Ctrl+Shift+T`` style combo into individual key tokens."""
    return [part for part in combo.replace(" ", "").split("+") if part]
