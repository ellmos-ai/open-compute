"""Tests for the safety / permission gate."""

from __future__ import annotations

from open_compute.actions import Action, ActionType
from open_compute.safety import Decision, SafetyPolicy


def test_screenshot_is_always_allowed_in_confirm_mode():
    policy = SafetyPolicy(mode="confirm")
    assert policy.evaluate(Action(ActionType.SCREENSHOT)).decision is Decision.ALLOW


def test_risky_action_requires_confirmation_without_callback():
    policy = SafetyPolicy(mode="confirm")
    result = policy.evaluate(Action(ActionType.LEFT_CLICK, x=0.5, y=0.5))
    assert result.decision is Decision.CONFIRM


def test_confirm_callback_allows():
    policy = SafetyPolicy(mode="confirm", confirm_callback=lambda a: True)
    result = policy.evaluate(Action(ActionType.LEFT_CLICK, x=0.5, y=0.5))
    assert result.decision is Decision.ALLOW


def test_confirm_callback_denies():
    policy = SafetyPolicy(mode="confirm", confirm_callback=lambda a: False)
    result = policy.evaluate(Action(ActionType.TYPE, text="rm -rf /"))
    assert result.decision is Decision.DENY


def test_allow_all_mode():
    policy = SafetyPolicy(mode="allow_all")
    assert policy.is_allowed(Action(ActionType.TYPE, text="hi"))


def test_read_only_mode_blocks_state_change():
    policy = SafetyPolicy(mode="read_only")
    assert policy.evaluate(Action(ActionType.LEFT_CLICK, x=0.1, y=0.1)).decision is Decision.DENY
    assert policy.evaluate(Action(ActionType.SCREENSHOT)).decision is Decision.ALLOW


def test_deny_list_overrides():
    policy = SafetyPolicy(mode="allow_all", denied_actions=frozenset({ActionType.KEY}))
    assert policy.evaluate(Action(ActionType.KEY, text="ctrl+w")).decision is Decision.DENY


def test_audit_log_records_every_evaluation():
    policy = SafetyPolicy(mode="allow_all")
    policy.evaluate(Action(ActionType.SCREENSHOT))
    policy.evaluate(Action(ActionType.TYPE, text="a"))
    assert len(policy.audit_log) == 2
