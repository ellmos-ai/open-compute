"""Turn a flat RawEvent stream into semantic clirec Steps.

Pure standard library. UIA enrichment is optional (probe may be None).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .capture.base import RawEvent
from .format import Step

_DRAG_THRESHOLD_PX = 5


@runtime_checkable
class UiaProbeLike(Protocol):
    def element_at(self, x: int, y: int) -> dict | None: ...
    def is_password_focused(self) -> bool: ...


def _enrich(step: Step, probe, x, y) -> Step:
    if probe is None or x is None:
        return step
    el = probe.element_at(x, y)
    if el:
        step.ui_name = el.get("name")
        step.ui_window = el.get("window")
        step.ui_role = el.get("role")
    return step


def events_to_steps(events, *, probe=None, mask_passwords: bool = True) -> list[Step]:
    steps: list[Step] = []
    idx = 0
    i = 0
    n = len(events)
    pending_down: RawEvent | None = None

    def add(step: Step) -> None:
        nonlocal idx
        idx += 1
        step.index = idx
        steps.append(step)

    while i < n:
        e = events[i]
        if e.kind == "mouse_down":
            pending_down = e
            i += 1
            continue
        if e.kind == "mouse_up" and pending_down is not None:
            d = pending_down
            pending_down = None
            dx = abs((e.x or 0) - (d.x or 0))
            dy = abs((e.y or 0) - (d.y or 0))
            if dx <= _DRAG_THRESHOLD_PX and dy <= _DRAG_THRESHOLD_PX:
                s = Step(index=0, t=d.t, action="click", x=d.x, y=d.y, btn=d.button)
            else:
                s = Step(index=0, t=d.t, action="left_click_drag",
                         x=d.x, y=d.y, end_x=e.x, end_y=e.y, btn=d.button)
            add(_enrich(s, probe, d.x, d.y))
            i += 1
            continue
        if e.kind == "char":
            j = i
            buf: list[str] = []
            while j < n and events[j].kind == "char":
                buf.append(events[j].char or "")
                j += 1
            text = "".join(buf)
            if mask_passwords and probe is not None and probe.is_password_focused():
                text = "***"
            add(Step(index=0, t=e.t, action="type", text=text))
            i = j
            continue
        if e.kind == "wheel":
            amt = abs(e.delta or 0)
            direction = "up" if (e.delta or 0) > 0 else "down"
            add(_enrich(Step(index=0, t=e.t, action="scroll", x=e.x, y=e.y,
                             scroll_dir=direction, scroll_amount=amt), probe, e.x, e.y))
            i += 1
            continue
        if e.kind == "key_down" and e.key:
            add(Step(index=0, t=e.t, action="key", keys=e.key))
            i += 1
            continue
        i += 1  # ignore mouse_move / key_up / stray events
    return steps
