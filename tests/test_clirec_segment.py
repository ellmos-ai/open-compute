from open_compute.clirec.capture.base import RawEvent
from open_compute.clirec.segment import events_to_steps


class FakeProbe:
    def __init__(self, pw=False, elem=None):
        self._pw = pw
        self._elem = elem or {"name": "Btn", "window": "Win", "role": "button"}
    def element_at(self, x, y):
        return self._elem
    def is_password_focused(self):
        return self._pw


def test_click_from_down_up_same_point():
    evts = [RawEvent("mouse_down", 0.0, x=100, y=50, button="left"),
            RawEvent("mouse_up", 0.05, x=101, y=50, button="left")]
    steps = events_to_steps(evts, probe=FakeProbe())
    assert len(steps) == 1
    assert steps[0].action == "click" and steps[0].x == 100 and steps[0].btn == "left"
    assert steps[0].ui_name == "Btn" and steps[0].ui_role == "button"


def test_drag_when_points_differ():
    evts = [RawEvent("mouse_down", 0.0, x=10, y=10, button="left"),
            RawEvent("mouse_up", 0.2, x=200, y=80, button="left")]
    steps = events_to_steps(evts)
    assert steps[0].action == "left_click_drag"
    assert (steps[0].end_x, steps[0].end_y) == (200, 80)


def test_chars_merge_into_type():
    evts = [RawEvent("char", 0.0, char="h"), RawEvent("char", 0.1, char="i")]
    steps = events_to_steps(evts)
    assert len(steps) == 1 and steps[0].action == "type" and steps[0].text == "hi"


def test_password_focus_masks_text():
    evts = [RawEvent("char", 0.0, char="s"), RawEvent("char", 0.1, char="e"),
            RawEvent("char", 0.2, char="c")]
    steps = events_to_steps(evts, probe=FakeProbe(pw=True))
    assert steps[0].text == "***"


def test_named_key_becomes_key_step():
    evts = [RawEvent("key_down", 0.0, key="enter")]
    steps = events_to_steps(evts)
    assert steps[0].action == "key" and steps[0].keys == "enter"
