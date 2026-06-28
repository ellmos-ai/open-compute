"""Replay a Recording against an Executor: dumb-first, adaptive fallback.

Pure standard library. The Executor protocol comes from
open_compute.drivers.base; coordinates are normalized via the recording's
own resolution so the executor can re-denormalize to the live screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..actions import Action, ActionType
from .format import Recording, Step, apply_params

_CLICK_BTN = {
    "left": ActionType.LEFT_CLICK,
    "right": ActionType.RIGHT_CLICK,
    "middle": ActionType.MIDDLE_CLICK,
}


@dataclass
class ReplayReport:
    total: int = 0
    ok: int = 0
    fallbacks: int = 0
    failures: list[str] = field(default_factory=list)


def _norm(px: int, span: int) -> float:
    if span <= 0:
        return 0.0
    return max(0.0, min(1.0, px / span))


def _res(rec: Recording) -> tuple[int, int]:
    try:
        w, h = rec.resolution.lower().split("x")
        return int(w), int(h)
    except Exception:
        return (0, 0)


def _to_action(step: Step, nx: float | None, ny: float | None,
               end: tuple[float | None, float | None] = (None, None)) -> Action:
    a = step.action
    if a == "click":
        return Action(_CLICK_BTN.get(step.btn or "left", ActionType.LEFT_CLICK), x=nx, y=ny)
    if a == "left_click_drag":
        return Action(ActionType.LEFT_CLICK_DRAG, x=nx, y=ny, end_x=end[0], end_y=end[1])
    if a == "type":
        return Action(ActionType.TYPE, text=step.text or "")
    if a == "key":
        return Action(ActionType.KEY, text=step.keys or "")
    if a == "scroll":
        return Action(ActionType.SCROLL, x=nx, y=ny,
                      scroll_direction=step.scroll_dir or "down",
                      scroll_amount=step.scroll_amount or 1)
    raise ValueError(f"unsupported action for replay: {a!r}")


def replay(rec: Recording, executor, *, params=None, locate=None) -> ReplayReport:
    """Replay *rec* against *executor*, returning a :class:`ReplayReport`.

    Args:
        rec: The recording to replay.
        executor: Object with ``width``, ``height``, and ``execute(Action)`` method.
        params: Optional substitution dict applied via :func:`apply_params`.
        locate: Optional callable ``(step) -> (float, float) | None`` that
            returns a normalized point when the UIA element is re-found; used
            as adaptive fallback when dumb coordinate execution fails.

    Returns:
        A :class:`ReplayReport` with total/ok/fallbacks/failures counts.
    """
    if params:
        rec = apply_params(rec, params)
    rw, rh = _res(rec)
    report = ReplayReport()
    for step in rec.steps:
        report.total += 1
        nx = _norm(step.x, rw) if step.x is not None else None
        ny = _norm(step.y, rh) if step.y is not None else None
        enx = _norm(step.end_x, rw) if step.end_x is not None else None
        eny = _norm(step.end_y, rh) if step.end_y is not None else None
        try:
            executor.execute(_to_action(step, nx, ny, end=(enx, eny)))
            report.ok += 1
            continue
        except Exception as first_exc:
            relocated = locate(step) if (locate is not None and step.x is not None) else None
            if relocated is not None:
                try:
                    executor.execute(_to_action(step, relocated[0], relocated[1],
                                                end=(enx, eny)))
                    report.ok += 1
                    report.fallbacks += 1
                    continue
                except Exception as second_exc:
                    report.failures.append(f"step {step.index}: {second_exc}")
                    continue
            report.failures.append(f"step {step.index}: {first_exc}")
    return report
