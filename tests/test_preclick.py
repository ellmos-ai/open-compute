"""Fail-closed coordinate-click verification; all Win32/input is mocked."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from open_compute.actions import Action, ActionType
from open_compute.perception import Observation
from open_compute.preclick import (
    PreClickVerificationError,
    Win32WindowProbe,
    execute_with_preclick,
)


EXPECTED = {"hwnd": 42, "pid": 7001, "title": "Target - Editor"}
FRAME = {"left": -1920, "top": 0, "width": 1920, "height": 1080}


@dataclass
class _Executor:
    width: int = 1920
    height: int = 1080
    executed: list[Action] = field(default_factory=list)

    @property
    def coordinate_frame(self) -> tuple[int, int, int, int]:
        return (-1920, 0, self.width, self.height)

    def execute(self, action: Action) -> Observation:
        self.executed.append(action)
        return Observation(screenshot=b"png", width=self.width, height=self.height)


class _Probe:
    def __init__(self, identity: dict | None):
        self.identity = identity
        self.points: list[tuple[int, int]] = []

    def window_at_point(self, x: int, y: int) -> dict | None:
        self.points.append((x, y))
        return self.identity


class _SequenceProbe(_Probe):
    def __init__(self, identities: list[dict | None]):
        super().__init__(None)
        self.identities = iter(identities)

    def window_at_point(self, x: int, y: int) -> dict | None:
        self.points.append((x, y))
        return next(self.identities)


def _click() -> Action:
    return Action(ActionType.LEFT_CLICK, x=0.5, y=0.5)


def test_matching_identity_allows_exactly_one_backend_call() -> None:
    executor = _Executor()
    probe = _Probe(dict(EXPECTED))

    execute_with_preclick(
        executor,
        _click(),
        expected_window=EXPECTED,
        coordinate_frame=FRAME,
        probe=probe,
    )

    assert len(executor.executed) == 1
    assert probe.points == [(-960, 540)]


def test_mismatch_is_structured_and_backend_is_never_called() -> None:
    executor = _Executor()
    actual = {"hwnd": 99, "pid": 7002, "title": "Foreign - Browser"}

    with pytest.raises(PreClickVerificationError) as caught:
        execute_with_preclick(
            executor,
            _click(),
            expected_window=EXPECTED,
            coordinate_frame=FRAME,
            probe=_Probe(actual),
        )

    assert caught.value.code == "window_identity_mismatch"
    assert caught.value.to_result()["actual_window"] == actual
    assert executor.executed == []


def test_unresolvable_point_is_structured_and_backend_is_never_called() -> None:
    executor = _Executor()

    with pytest.raises(PreClickVerificationError) as caught:
        execute_with_preclick(
            executor,
            _click(),
            expected_window=EXPECTED,
            coordinate_frame=FRAME,
            probe=_Probe(None),
        )

    assert caught.value.code == "window_at_point_unresolvable"
    assert executor.executed == []


def test_missing_robust_expected_identity_is_fail_closed() -> None:
    executor = _Executor()

    with pytest.raises(PreClickVerificationError) as caught:
        execute_with_preclick(
            executor,
            _click(),
            expected_window={"hwnd": 42, "title": "Target - Editor"},
            coordinate_frame=FRAME,
            probe=_Probe(dict(EXPECTED)),
        )

    assert caught.value.code == "expected_window_invalid"
    assert executor.executed == []


def test_missing_coordinate_frame_is_fail_closed() -> None:
    executor = _Executor()

    with pytest.raises(PreClickVerificationError) as caught:
        execute_with_preclick(
            executor, _click(), expected_window=EXPECTED, probe=_Probe(dict(EXPECTED))
        )

    assert caught.value.code == "coordinate_frame_required"
    assert executor.executed == []


def test_window_local_capture_coordinates_are_rebased_to_virtual_desktop() -> None:
    executor = _Executor(width=3840, height=1080)
    source = {"left": 100, "top": 200, "width": 800, "height": 500}
    probe = _Probe(dict(EXPECTED))

    execute_with_preclick(
        executor,
        _click(),
        expected_window=EXPECTED,
        coordinate_frame=source,
        probe=probe,
    )

    transformed = executor.executed[0]
    assert probe.points == [(500, 450)]
    assert transformed.x == pytest.approx((500 - (-1920)) / 3839)
    assert transformed.y == pytest.approx(450 / 1079)


def test_drag_endpoint_mismatch_is_fail_closed_before_backend() -> None:
    executor = _Executor()
    foreign = {"hwnd": 99, "pid": 7002, "title": "Foreign - Browser"}
    drag = Action(
        ActionType.LEFT_CLICK_DRAG,
        x=0.25,
        y=0.25,
        end_x=0.75,
        end_y=0.75,
    )
    probe = _SequenceProbe([foreign, dict(EXPECTED)])

    with pytest.raises(PreClickVerificationError) as caught:
        execute_with_preclick(
            executor,
            drag,
            expected_window=EXPECTED,
            coordinate_frame=FRAME,
            probe=probe,
        )

    assert caught.value.code == "window_identity_mismatch"
    assert caught.value.point_px == (-481, 809)
    assert probe.points == [(-481, 809)]  # drag end is checked before its start
    assert executor.executed == []


def test_non_click_action_remains_backward_compatible_without_identity() -> None:
    executor = _Executor()
    move = Action(ActionType.MOUSE_MOVE, x=0.5, y=0.5)

    execute_with_preclick(executor, move, probe=_Probe(None))

    assert executor.executed == [move]


class _FakeUser32:
    def __init__(self) -> None:
        self.ancestor_calls: list[tuple[int, int]] = []

    def WindowFromPoint(self, _point):
        return 101  # child/overlay handle

    def GetAncestor(self, hwnd, flag):
        self.ancestor_calls.append((int(hwnd), int(flag)))
        return 42

    def GetWindowThreadProcessId(self, hwnd, pid_ptr):
        assert int(hwnd) == 42
        pid_ptr._obj.value = 7001
        return 9

    def GetWindowTextLengthW(self, hwnd):
        assert int(hwnd) == 42
        return len(EXPECTED["title"])

    def GetWindowTextW(self, hwnd, buf, _size):
        assert int(hwnd) == 42
        buf.value = EXPECTED["title"]
        return len(buf.value)


def test_window_from_point_resolves_child_to_top_level_root() -> None:
    user32 = _FakeUser32()

    actual = Win32WindowProbe(user32=user32).window_at_point(400, 300)

    assert actual == EXPECTED
    assert user32.ancestor_calls == [(101, 2)]  # GA_ROOT
