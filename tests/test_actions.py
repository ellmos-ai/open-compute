"""Tests for the canonical action schema and the per-backend mappers."""

from __future__ import annotations

import pytest

from open_compute.actions import Action, ActionType, to_claude, to_openai

W, H = 1280, 800


def test_point_action_requires_coordinates():
    with pytest.raises(ValueError):
        Action(ActionType.LEFT_CLICK)


def test_type_action_requires_text():
    with pytest.raises(ValueError):
        Action(ActionType.TYPE)


def test_normalized_range_enforced():
    with pytest.raises(ValueError):
        Action(ActionType.LEFT_CLICK, x=1.5, y=0.5)


def test_to_claude_left_click_uses_global_pixels():
    action = Action(ActionType.LEFT_CLICK, x=0.5, y=0.5)
    out = to_claude(action, W, H)
    assert out["action"] == "left_click"
    assert out["coordinate"] == [640, 400]


def test_to_claude_drag_has_start_and_end():
    action = Action(ActionType.LEFT_CLICK_DRAG, x=0.0, y=0.0, end_x=1.0, end_y=1.0)
    out = to_claude(action, W, H)
    assert out["start_coordinate"] == [0, 0]
    assert out["coordinate"] == [1279, 799]


def test_to_claude_type_and_key():
    assert to_claude(Action(ActionType.TYPE, text="hi"), W, H) == {
        "action": "type",
        "text": "hi",
    }
    assert to_claude(Action(ActionType.KEY, text="ctrl+s"), W, H) == {
        "action": "key",
        "text": "ctrl+s",
    }


def test_to_claude_scroll_defaults():
    out = to_claude(Action(ActionType.SCROLL, x=0.5, y=0.5), W, H)
    assert out["scroll_direction"] == "down"
    assert out["scroll_amount"] == 3


def test_host_side_action_has_no_claude_mapping():
    with pytest.raises(ValueError):
        to_claude(Action(ActionType.LAUNCH_APP, app_name="firefox"), W, H)


def test_to_openai_click_has_button_and_flat_xy():
    out = to_openai(Action(ActionType.LEFT_CLICK, x=0.5, y=0.5), W, H)
    assert out["type"] == "click"
    assert out["button"] == "left"
    assert out["x"] == 640
    assert out["y"] == 400


def test_to_openai_key_splits_combo():
    out = to_openai(Action(ActionType.KEY, text="ctrl+shift+t"), W, H)
    assert out["type"] == "keypress"
    assert out["keys"] == ["ctrl", "shift", "t"]


def test_to_openai_scroll_direction_to_delta():
    out = to_openai(
        Action(ActionType.SCROLL, x=0.5, y=0.5, scroll_direction="up", scroll_amount=2),
        W,
        H,
    )
    assert out["type"] == "scroll"
    assert out["scroll_y"] == -2


def test_host_side_action_has_no_openai_mapping():
    with pytest.raises(ValueError):
        to_openai(Action(ActionType.ACTIVATE_WINDOW, app_name="chrome"), W, H)
