"""Tests for the FastMCP server wrapper (open_compute.mcp_server).

Mock-only: a tiny in-process fake executor is injected via ``_STATE.set_executor``
so the whole suite runs on any platform without mss / a real desktop. The MCP
SDK is an optional extra, so the module is import-or-skipped.
"""

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("mcp")  # server needs the optional open-compute[mcp] extra

from mcp.types import CallToolResult, ImageContent  # noqa: E402

from open_compute import mcp_server as S  # noqa: E402
from open_compute.interaction import (  # noqa: E402
    InteractionContractError,
    describe_window,
    send_text_verified,
)


class _Obs:
    def __init__(self):
        self.screenshot = b"\x89PNG\r\n\x1a\nFAKE"
        self.width = 1920
        self.height = 1080


class _FakeExec:
    width = 1920
    height = 1080

    def __init__(self):
        self.executed = []

    def screenshot(self):
        return _Obs()

    def execute(self, action):
        self.executed.append(action)
        return _Obs()

    def type_text_verified(self, text, *, check_focus, expected_window):
        self.executed.append(("type", len(text)))
        return send_text_verified(
            text,
            send_chunk=lambda chunk: len(chunk),
            check_focus=check_focus,
            expected_window=expected_window,
        )

    def activate_window_identity(self, window):
        self.executed.append(("activate", window["window_id"]))


_COORDINATE_FRAME = {"left": 0, "top": 0, "width": 1920, "height": 1080}
_EXPECTED_WINDOW = {
    "hwnd": 42,
    "pid": 7001,
    "title": "Target - Editor",
    "foreground": True,
    "rect": dict(_COORDINATE_FRAME),
}
_PRECLICK = {
    "expected_window": _EXPECTED_WINDOW,
    "coordinate_frame": _COORDINATE_FRAME,
}


class _FakeWindowProbe:
    def __init__(self, identity=None):
        self.identity = identity or dict(_EXPECTED_WINDOW)

    def window_at_point(self, _x, _y):
        return dict(self.identity)


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.delenv("OC_SAFETY_MODE", raising=False)
    monkeypatch.delenv("OC_DENY", raising=False)
    S._STATE.set_executor(_FakeExec())
    S._STATE.set_preclick_probe(_FakeWindowProbe())
    def _fake_windows(*, issue_tokens=False):
        window = describe_window(_EXPECTED_WINDOW)
        if issue_tokens:
            S._STATE.window_tokens[window["window_token"]] = window
        return [window]
    monkeypatch.setattr(S, "_current_windows", _fake_windows)
    S.list_windows()  # issue the descriptor/token used by action tests
    _PRECLICK["observation_id"] = S.capture().structuredContent["observation_id"]
    yield


def _tool_names():
    return sorted(t.name for t in asyncio.run(S.mcp.list_tools()))


def test_tools_registered():
    assert _tool_names() == [
        "capture", "chat", "click_name", "do", "get_screen_size", "invoke",
        "list_windows", "push_status", "rec_replay", "signal_abort",
        "signal_hide", "signal_show", "signal_status", "talk", "tree",
        "watch_dir",
    ]


def test_do_schema_exposes_params():
    tools = asyncio.run(S.mcp.list_tools())
    do = next(t for t in tools if t.name == "do")
    props = (do.inputSchema or {}).get("properties", {})
    assert {
        "action", "actions", "mode", "expected_window", "coordinate_frame",
        "observation_id", "keep_signal",
    } <= set(props)


def test_capture_returns_image():
    result = S.capture()
    assert isinstance(result, CallToolResult)
    meta = result.structuredContent
    assert meta["observation_id"].startswith("obs_1_")
    assert meta["screenshot_id"] == meta["observation_id"]
    image = next(item for item in result.content if isinstance(item, ImageContent))
    assert image.data  # base64 PNG bytes passed through


def test_capture_serializes_through_fastmcp():
    result = asyncio.run(S.mcp.call_tool("capture", {}))
    assert isinstance(result, CallToolResult)
    assert result.structuredContent["observation_id"].startswith("obs_1_")
    assert any(isinstance(item, ImageContent) for item in result.content)


def test_click_confirm_gates_by_default():
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3}, **_PRECLICK)
    assert r["result"] == "needs_confirmation"
    assert r["action"] == "left_click"


def test_click_executes_when_server_allows(monkeypatch):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3}, **_PRECLICK)
    assert r["result"] == "executed"
    assert r["action"] == "left_click"
    assert r["post_action_observation"]["observation_id"].startswith("obs_1_")


def test_coordinate_observation_is_one_shot(monkeypatch):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    first = S.do(
        action={"type": "left_click", "x": 0.5, "y": 0.3}, **_PRECLICK
    )
    second = S.do(
        action={"type": "left_click", "x": 0.5, "y": 0.3}, **_PRECLICK
    )

    assert first["result"] == "executed"
    assert second["result"] == "interaction_rejected"
    assert second["code"] == "stale_observation"


@pytest.mark.parametrize("length", [100, 500, 2_000])
def test_type_reports_verified_character_counts(monkeypatch, length):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    result = S.do(
        action={"type": "type", "text": "x" * length},
        expected_window=_EXPECTED_WINDOW,
    )

    assert result["result"] == "executed"
    assert result["requested_chars"] == length
    assert result["sent_chars"] == length
    assert result["complete"] is True
    assert "text" not in result


def test_click_without_expected_identity_is_fail_closed(monkeypatch):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    executor = S._STATE.executor()

    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3})

    assert r["result"] == "interaction_rejected"
    assert r["code"] == "expected_window_required"
    assert executor.executed == []


def test_click_mismatch_never_reaches_backend(monkeypatch):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    executor = S._STATE.executor()
    S._STATE.set_preclick_probe(
        _FakeWindowProbe({"hwnd": 99, "pid": 7002, "title": "Foreign Browser"})
    )

    r = S.do(
        action={"type": "left_click", "x": 0.5, "y": 0.3}, **_PRECLICK
    )

    assert r["result"] == "preclick_verification_failed"
    assert r["code"] == "window_identity_mismatch"
    assert executor.executed == []


def test_mouse_move_requires_observation_and_window():
    r = S.do(
        action={"type": "mouse_move", "x": 0.5, "y": 0.5}, **_PRECLICK
    )
    assert r["result"] == "executed"


def test_read_only_denies_state_change():
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3}, mode="read_only")
    assert r["result"] == "deny"


def test_action_alias_key_accepted():
    # 'action' as an alias for 'type' (Claude-style dicts)
    r = S.do(
        action={"action": "mouse_move", "x": 0.4, "y": 0.4}, **_PRECLICK
    )
    assert r["result"] == "executed"


def test_coordinate_batch_is_rejected_before_execution(monkeypatch):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    r = S.do(
        actions=[{"type": "mouse_move", "x": 0.1, "y": 0.1},
                 {"type": "left_click", "x": 0.2, "y": 0.2}],
        **_PRECLICK,
    )
    assert r["result"] == "interaction_rejected"
    assert r["code"] == "one_action_per_observation_required"


def test_batch_stops_at_gate():
    r = S.do(
        actions=[{"type": "wait", "duration": 0.001},
                 {"type": "launch_app", "app_name": "notepad"}],
    )
    assert r["result"] == "needs_confirmation"
    assert r["action_index"] == 1
    assert r["executed_before"] == 1


def test_denylist_env(monkeypatch):
    monkeypatch.setenv("OC_DENY", "type")
    r = S.do(action={"type": "type", "text": "hi"}, mode="allow_all")
    assert r["result"] == "deny"


def test_invalid_action_raises():
    with pytest.raises(ValueError):
        S.do(action={"type": "left_click"})  # missing x/y


def test_requires_exactly_one_of_action_or_actions():
    with pytest.raises(ValueError):
        S.do()
    with pytest.raises(ValueError):
        S.do(action={"type": "wait", "duration": 1},
             actions=[{"type": "wait", "duration": 1}])


def test_default_mode_env_applies(monkeypatch):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3}, **_PRECLICK)
    assert r["result"] == "executed"


def test_watch_dir_returns_list(tmp_path):
    r = S.watch_dir(paths=[str(tmp_path)], once=True)
    assert isinstance(r, list)


def test_watch_dir_rejects_non_dir(tmp_path):
    with pytest.raises(ValueError):
        S.watch_dir(paths=[str(tmp_path / "does_not_exist")], once=True)


# --- OC_SAFETY_MODE is an operator ceiling; per-call mode can only tighten ---

def test_read_only_ceiling_not_loosened_by_per_call(monkeypatch):
    monkeypatch.setenv("OC_SAFETY_MODE", "read_only")
    # a prompt-injected agent must NOT escape read_only via mode="allow_all"
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3}, mode="allow_all")
    assert r["result"] == "deny"


def test_confirm_ceiling_not_loosened_by_per_call(monkeypatch):
    monkeypatch.setenv("OC_SAFETY_MODE", "confirm")
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3}, mode="allow_all")
    assert r["result"] == "needs_confirmation"


def test_per_call_mode_can_tighten(monkeypatch):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3}, mode="read_only")
    assert r["result"] == "deny"


# ---------------------------------------------------------------------------
# Signal / chat / talk tools (human-in-the-loop)
# ---------------------------------------------------------------------------

class _FakeOverlay:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.shown = []
        self.visible = False

    def show(self, *, color, label, border=True, cursor=True):
        self.shown.append({"color": color, "label": label,
                           "border": border, "cursor": cursor})
        self.visible = True

    def hide(self):
        self.visible = False

    def is_visible(self):
        return self.visible


class _FakePromptChannel:
    message = "fake message"

    def prompt_reason(self, *, context):
        return self.message


def _reset_signal_state():
    S._cancel_idle_hide()  # never let a live timer leak into the next test
    S._cancel_signal_lease()
    S._STATE.signal_indicator = None
    S._STATE.signal_mode = ""
    S._STATE.pending_abort_message = None
    S._STATE.signal_auto_shown = False
    S._STATE.signal_owner = ""
    S._STATE.signal_session = ""
    S._STATE.signal_expires_at = None
    # Not-Aus / grace state (Ticket T-20260818-895473048) — a leaked armed
    # grace_deadline would silently block the *next* test's do()/capture()
    # call for real wall-clock seconds, so this reset is not optional.
    S._STATE.abort_triggered = False
    S._STATE.abort_reason = None
    S._STATE.grace_deadline = None
    S._STATE.activity_classifier = None
    S._STATE.activity_adapter = None


@pytest.fixture
def _signal_state(monkeypatch):
    """Reset signal state and inject a fake overlay for every test here."""
    monkeypatch.setattr(S, "WindowsBorderOverlay", _FakeOverlay, raising=False)
    _reset_signal_state()
    yield
    _reset_signal_state()


def test_signal_show_and_hide(_signal_state):
    result = S.signal_show(
        mode="control",
        agent="kimi",
        owner="codex",
        session_id="test-session",
    )
    assert result["visible"] is True
    assert result["mode"] == "control"
    assert result["color"] == [255, 40, 60]
    assert "kimi" in result["label"]
    assert result["owner"] == "codex"
    assert result["session"] == "test-session"
    assert result["expires_at"]

    status = S.signal_status()
    assert status["visible"] is True
    assert status["mode"] == "control"
    assert status["owner"] == "codex"
    assert status["session"] == "test-session"
    assert status["pending_abort_message"] is None

    hidden = S.signal_hide()
    assert hidden == {"visible": False}
    assert S.signal_status()["visible"] is False


def test_signal_lease_expires_and_cleans_up(_signal_state):
    S.signal_show(mode="control", ttl_seconds=0.05)
    timer = S._STATE.signal_lease_timer
    assert timer is not None
    timer.join(2.0)
    assert not timer.is_alive()
    status = S.signal_status()
    assert status["visible"] is False
    assert status["owner"] == ""
    assert status["session"] == ""
    assert status["expires_at"] is None


def test_action_turn_end_hides_auto_signal(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "control")
    result = S.do(
        action={"type": "left_click", "x": 0.5, "y": 0.3}, **_PRECLICK
    )
    assert result["result"] == "executed"
    assert S._STATE.signal_indicator is None
    assert S._STATE.signal_lease_timer is None


def test_auto_signal_replaces_orphaned_invisible_overlay(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "control")
    monkeypatch.setenv("OC_SIGNAL_GRACE_SECONDS", "0")
    stale = S.signal_show(mode="observe", ttl_seconds=30)
    assert stale["visible"] is True
    S._STATE.signal_indicator.renderer.visible = False

    result = S.do(
        action={"type": "left_click", "x": 0.5, "y": 0.3},
        keep_signal=True,
        **_PRECLICK,
    )
    assert result["result"] == "executed"
    assert S.signal_status()["visible"] is True
    assert S._STATE.signal_mode == "control"


def test_action_error_hides_manual_signal(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SIGNAL_GRACE_SECONDS", "0")
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    S.signal_show(mode="control")
    result = S.do(
        action={"type": "left_click", "x": 0.5, "y": 0.3},
        expected_window=_EXPECTED_WINDOW,
    )
    assert result["code"] == "observation_required"
    assert S._STATE.signal_indicator is None


def test_invalid_action_schema_hides_manual_signal(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SIGNAL_GRACE_SECONDS", "0")
    S.signal_show(mode="control")
    with pytest.raises(ValueError):
        S.do(action={"type": "left_click"})
    assert S._STATE.signal_indicator is None


def test_signal_show_rejects_unknown_mode(_signal_state):
    import pytest as _pt
    with _pt.raises(ValueError):
        S.signal_show(mode="not-a-mode")


def test_signal_status_consumes_pending_abort(_signal_state):
    S._STATE.pending_abort_message = "stop, wrong window"
    first = S.signal_status()
    assert first["pending_abort_message"] == "stop, wrong window"
    assert S.signal_status()["pending_abort_message"] is None


def test_signal_show_config_toggles(monkeypatch, _signal_state, tmp_path):
    from open_compute.indicator import SignalConfig

    cfg = SignalConfig.from_dict({
        "thickness": 9,
        "modes": {"control": {"border": False, "cursor": True}},
    })
    path = tmp_path / "cfg.json"
    cfg.save(path)
    result = S.signal_show(mode="control", agent="kimi",
                           config_path=str(path))
    assert result["visible"] is True
    overlay = S._STATE.signal_indicator.renderer
    assert overlay.kwargs["thickness"] == 9
    assert overlay.shown[-1]["border"] is False
    assert overlay.shown[-1]["cursor"] is True


def test_signal_abort_uses_channel(monkeypatch, _signal_state):
    monkeypatch.setattr(S, "TkAbortChannel", _FakePromptChannel, raising=False)
    result = S.signal_abort(context="ctx")
    assert result == {"abort_message": "fake message"}


def test_chat_returns_message_and_shot(monkeypatch, _signal_state, tmp_path):
    monkeypatch.setattr(S, "TkAbortChannel", _FakePromptChannel, raising=False)
    monkeypatch.setattr(S, "_module_session_dir", lambda: tmp_path)
    result = S.chat(channel="tk", shot=True)
    assert result["chat_message"] == "fake message"
    assert result["screenshot"] is not None
    assert (tmp_path / result["screenshot"].split(tmp_path.name + "\\")[-1]
            if "\\" in result["screenshot"] else True)
    import pathlib
    assert pathlib.Path(result["screenshot"]).exists()


def test_chat_without_shot(monkeypatch, _signal_state):
    monkeypatch.setattr(S, "TkAbortChannel", _FakePromptChannel, raising=False)
    assert S.chat(channel="tk") == {
        "chat_message": "fake message", "screenshot": None,
    }


def test_talk_uses_injected_recorder(monkeypatch, _signal_state, tmp_path):
    from open_compute.talk import TalkResult

    seen = {}

    def fake_rpt(*, vk, out_path, mci, key_down, max_seconds, wait_timeout):
        seen.update(vk=vk, max_seconds=max_seconds)
        return TalkResult(True, out_path, 2.0, "released")

    monkeypatch.setattr(S, "record_push_to_talk", fake_rpt, raising=False)
    monkeypatch.setattr(S, "winmm_mci", lambda: (lambda _c: 0), raising=False)
    monkeypatch.setattr(
        S, "async_key_down", lambda: (lambda _v: False), raising=False
    )
    monkeypatch.setattr(S, "_module_session_dir", lambda: tmp_path)

    result = S.talk(key="F9", max_seconds=5.0)
    assert result["recorded"] is True
    assert result["reason"] == "released"
    assert result["wav"].startswith(str(tmp_path))
    assert seen == {"vk": 0x78, "max_seconds": 5.0}


def test_talk_rejects_modifier_key(_signal_state):
    import pytest as _pt
    with _pt.raises(ValueError):
        S.talk(key="ctrl+a")


# ---------------------------------------------------------------------------
# OC_SIGNAL_AUTO — auto-show the overlay once a state-changing tool acts
# ---------------------------------------------------------------------------

class _FakeTarget:
    """Stand-in for feeds.base.Target, enough for click_name/invoke."""

    name = "Einfuegen"
    role = "Button"
    rect_px = (10, 10, 40, 20)
    center_norm = (0.5, 0.3)


class _FakeUiaFeed:
    def __init__(self, target=None, invoke_result=True):
        self.target = target or _FakeTarget()
        self.invoke_result = invoke_result
        self.invoked = []

    def resolve(self, query, window=None):
        return self.target

    def observe(self, window=None):
        return SimpleNamespace(elements=[{
            "name": self.target.name,
            "role": self.target.role,
            "value": "",
            "rect_px": self.target.rect_px,
            "visible": True,
            "depth": 1,
        }])

    def invoke(self, query, window=None):
        self.invoked.append((query, window))
        return self.invoke_result


@pytest.fixture(autouse=True)
def _clean_signal_auto_env(monkeypatch):
    """OC_SIGNAL_AUTO must not leak in from (or out to) the real environment."""
    monkeypatch.delenv("OC_SIGNAL_AUTO", raising=False)
    monkeypatch.delenv("OC_SIGNAL_IDLE_HIDE", raising=False)
    monkeypatch.delenv("OC_SIGNAL_GRACE_SECONDS", raising=False)
    monkeypatch.delenv("OC_HUMAN_ACTIVITY_WATCH", raising=False)
    yield


def test_auto_signal_off_by_default(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3}, **_PRECLICK)
    assert r["result"] == "executed"
    assert "auto_signal_error" not in r
    assert S._STATE.signal_indicator is None


def test_auto_signal_off_value_disables(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "off")
    S.do(action={"type": "left_click", "x": 0.5, "y": 0.3}, **_PRECLICK)
    assert S._STATE.signal_indicator is None


def test_auto_signal_shows_after_executed_action(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "control")
    r = S.do(
        action={"type": "left_click", "x": 0.5, "y": 0.3},
        keep_signal=True,
        **_PRECLICK,
    )
    assert r["result"] == "executed"
    assert "auto_signal_error" not in r
    assert S._STATE.signal_indicator is not None
    assert S._STATE.signal_mode == "control"


def test_auto_signal_does_not_fire_when_action_is_gated(monkeypatch, _signal_state):
    # default confirm mode: the click is gated, nothing actually ran
    monkeypatch.setenv("OC_SIGNAL_AUTO", "control")
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3})
    assert r["result"] == "needs_confirmation"
    assert S._STATE.signal_indicator is None


def test_auto_signal_read_only_tool_never_triggers(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SIGNAL_AUTO", "control")
    S.capture()
    assert S._STATE.signal_indicator is None


def test_auto_signal_does_not_override_existing_manual_signal(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SIGNAL_GRACE_SECONDS", "0")  # not under test here
    S.signal_show(mode="observe", agent="human")
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "control")
    S.do(
        action={"type": "left_click", "x": 0.5, "y": 0.3},
        keep_signal=True,
        **_PRECLICK,
    )
    # a manually shown signal (any mode) is never overridden by auto-signal
    assert S._STATE.signal_mode == "observe"


def test_auto_signal_invalid_mode_reports_error_without_crashing(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "not-a-mode")
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3}, **_PRECLICK)
    # the action itself must still succeed — a bad auto-signal config must
    # never crash or block the tool it is attached to
    assert r["result"] == "executed"
    assert "not-a-mode" in r["auto_signal_error"]
    assert S._STATE.signal_indicator is None


def test_auto_signal_fires_once_for_a_batch(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "control")
    r = S.do(
        actions=[
            {"type": "wait", "duration": 0.001},
            {"type": "wait", "duration": 0.001},
        ],
        keep_signal=True,
    )
    assert r["result"] == "batch"
    assert S._STATE.signal_mode == "control"
    # only one overlay call — the second gate pass sees signal already visible
    assert len(S._STATE.signal_indicator.renderer.shown) == 1


def test_auto_signal_click_name(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "control")
    monkeypatch.setattr(S, "_load_uia_feed", lambda *a, **k: _FakeUiaFeed())
    r = S.click_name("Einfuegen", window=_EXPECTED_WINDOW, keep_signal=True)
    assert r["result"] == "executed"
    assert r["match_type"] == "exact"
    assert r["score"] == 1.0
    assert r["alternatives"] == []
    assert S._STATE.signal_mode == "control"


def test_click_name_requires_previously_issued_window(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setattr(S, "_load_uia_feed", lambda *a, **k: _FakeUiaFeed())
    result = S.click_name("Einfuegen")
    assert result["code"] == "expected_window_required"
    assert S._STATE.executor().executed == []


def test_click_name_returns_structured_ambiguity(monkeypatch, _signal_state):
    class _AmbiguousFeed:
        def resolve_detailed(self, *_args, **_kwargs):
            raise InteractionContractError(
                "ambiguous_target",
                "multiple strongest matches",
                candidates=[{"name": "Save"}, {"name": "Save"}],
            )

    monkeypatch.setattr(S, "_load_uia_feed", lambda *a, **k: _AmbiguousFeed())
    result = S.click_name("Save", window=_EXPECTED_WINDOW)
    assert result["status"] == "AMBIGUOUS_TARGET"
    assert len(result["candidates"]) == 2


def test_click_name_rejects_element_that_moves_before_dispatch(
    monkeypatch, _signal_state
):
    class _MovingFeed:
        calls = 0

        def resolve_detailed(self, *_args, **_kwargs):
            self.calls += 1
            return SimpleNamespace(
                name="Erstellen",
                role="Button",
                rect_px=(10 + self.calls, 10, 40, 20),
                center_norm=(0.5, 0.3),
                match_type="exact",
                score=1.0,
                alternatives=(),
            )

    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setattr(S, "_load_uia_feed", lambda *a, **k: _MovingFeed())
    result = S.click_name("Erstellen", window=_EXPECTED_WINDOW)
    assert result["code"] == "target_changed"
    assert S._STATE.executor().executed == []


def test_tree_observation_can_drive_exactly_one_coordinate_action(
    monkeypatch, _signal_state
):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setattr(S, "_load_uia_feed", lambda *a, **k: _FakeUiaFeed())
    tree_result = S.tree()
    result = S.do(
        action={"type": "mouse_move", "x": 0.5, "y": 0.3},
        expected_window=_EXPECTED_WINDOW,
        observation_id=tree_result["observation_id"],
    )
    assert result["result"] == "executed"
    assert result["post_action_observation"]["kind"] == "screenshot"


def test_auto_signal_invoke(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "control")
    monkeypatch.setattr(S, "_load_uia_feed", lambda *a, **k: _FakeUiaFeed())
    r = S.invoke("Einfuegen", window=_EXPECTED_WINDOW, keep_signal=True)
    assert r["result"] == "invoked"
    assert S._STATE.signal_mode == "control"


def test_auto_signal_rec_replay(monkeypatch, _signal_state):
    # rec_replay() does `from . import cli` *inside* the function body, so
    # faking it means patching the real open_compute.cli module attribute,
    # not anything on the mcp_server module (S.cli does not exist).
    import open_compute.cli as oc_cli

    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "control")
    monkeypatch.setattr(
        oc_cli, "_run_replay",
        lambda path, params, executor, policy=None, abort_check=None: {"steps": 1},
    )
    r = S.rec_replay("dummy.clirec", keep_signal=True)
    assert r["result"] == "replayed"
    assert S._STATE.signal_mode == "control"


# ---------------------------------------------------------------------------
# OC_SIGNAL_IDLE_HIDE — take the auto-shown overlay down once steering stops
# ---------------------------------------------------------------------------

def _click(**_kw):
    current = {
        "expected_window": _EXPECTED_WINDOW,
        "coordinate_frame": _COORDINATE_FRAME,
        "observation_id": S.capture().structuredContent["observation_id"],
        "keep_signal": True,
    }
    current.update(_kw)
    return S.do(action={"type": "left_click", "x": 0.5, "y": 0.3}, **current)


def _await_idle_hide(timer, timeout=5.0):
    """Block until the armed idle timer has run (no polling loop)."""
    assert timer is not None, "expected an armed idle-hide timer"
    timer.join(timeout)
    assert not timer.is_alive(), "idle-hide timer did not fire in time"


@pytest.fixture
def _auto_signal_on(monkeypatch):
    """Auto-signal armed and the safety gate open — the steering case."""
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "control")


def test_idle_hide_arms_by_default_after_auto_show(_signal_state, _auto_signal_on):
    # OC_SIGNAL_IDLE_HIDE unset => the 60 s default, not "off"
    _click()
    assert S._STATE.signal_auto_shown is True
    timer = S._STATE.signal_idle_timer
    assert timer is not None
    assert timer.interval == 60.0


def test_idle_hide_hides_the_auto_overlay_when_it_fires(
    monkeypatch, _signal_state, _auto_signal_on
):
    monkeypatch.setenv("OC_SIGNAL_IDLE_HIDE", "0.05")
    _click()
    indicator = S._STATE.signal_indicator
    assert indicator is not None
    _await_idle_hide(S._STATE.signal_idle_timer)

    assert S._STATE.signal_indicator is None
    assert S._STATE.signal_mode == ""
    assert S._STATE.signal_auto_shown is False
    assert S._STATE.signal_idle_timer is None
    assert indicator.renderer.is_visible() is False


def test_idle_hide_rearms_on_every_action(monkeypatch, _signal_state, _auto_signal_on):
    monkeypatch.setenv("OC_SIGNAL_IDLE_HIDE", "30")
    _click()
    first = S._STATE.signal_idle_timer
    _click()
    second = S._STATE.signal_idle_timer

    # a fresh countdown per action, and the stale one really stopped
    assert second is not None and second is not first
    first.join(0.5)
    assert not first.is_alive()
    assert S._STATE.signal_indicator is not None  # nothing hidden in between


def test_idle_hide_rearms_via_click_name_and_invoke(
    monkeypatch, _signal_state, _auto_signal_on
):
    monkeypatch.setenv("OC_SIGNAL_IDLE_HIDE", "30")
    monkeypatch.setattr(S, "_load_uia_feed", lambda *a, **k: _FakeUiaFeed())
    _click()
    after_do = S._STATE.signal_idle_timer

    S.click_name("Einfuegen", window=_EXPECTED_WINDOW, keep_signal=True)
    after_click = S._STATE.signal_idle_timer
    assert after_click is not None and after_click is not after_do

    S.invoke("Einfuegen", window=_EXPECTED_WINDOW, keep_signal=True)
    after_invoke = S._STATE.signal_idle_timer
    assert after_invoke is not None and after_invoke is not after_click


@pytest.mark.parametrize("value", ["0", "", "off", "OFF", "-5"])
def test_idle_hide_disabled_values_keep_todays_behaviour(
    monkeypatch, _signal_state, _auto_signal_on, value
):
    monkeypatch.setenv("OC_SIGNAL_IDLE_HIDE", value)
    r = _click()
    assert r["result"] == "executed"
    assert "signal_idle_hide_error" not in r
    assert S._STATE.signal_indicator is not None  # overlay stays up, as before
    assert S._STATE.signal_idle_timer is None


def test_idle_hide_env_sets_the_countdown(monkeypatch, _signal_state, _auto_signal_on):
    monkeypatch.setenv("OC_SIGNAL_IDLE_HIDE", "12.5")
    _click()
    assert S._STATE.signal_idle_timer.interval == 12.5


def test_idle_hide_invalid_value_reports_error_without_breaking_the_action(
    monkeypatch, _signal_state, _auto_signal_on
):
    monkeypatch.setenv("OC_SIGNAL_IDLE_HIDE", "soon")
    r = _click()
    assert r["result"] == "executed"  # the action must never suffer for it
    assert "soon" in r["signal_idle_hide_error"]
    assert S._STATE.signal_indicator is not None
    assert S._STATE.signal_idle_timer is None


def test_manual_signal_show_is_never_swept_away(
    monkeypatch, _signal_state, _auto_signal_on
):
    monkeypatch.setenv("OC_SIGNAL_IDLE_HIDE", "0.05")
    monkeypatch.setenv("OC_SIGNAL_GRACE_SECONDS", "0")  # not under test here
    S.signal_show(mode="observe", agent="human")
    _click()  # auto-signal sees a visible overlay and leaves it alone

    assert S._STATE.signal_auto_shown is False
    assert S._STATE.signal_idle_timer is None
    time.sleep(0.2)  # well past the idle window
    assert S._STATE.signal_indicator is not None
    assert S._STATE.signal_mode == "observe"


def test_manual_show_takes_over_an_auto_shown_overlay(
    monkeypatch, _signal_state, _auto_signal_on
):
    monkeypatch.setenv("OC_SIGNAL_IDLE_HIDE", "0.05")
    _click()
    armed = S._STATE.signal_idle_timer
    assert armed is not None

    S.signal_show(mode="observe", agent="human")  # human takes ownership
    assert S._STATE.signal_auto_shown is False
    assert S._STATE.signal_idle_timer is None
    armed.join(0.5)
    assert not armed.is_alive()

    time.sleep(0.2)
    assert S._STATE.signal_indicator is not None
    assert S._STATE.signal_mode == "observe"


def test_signal_hide_cancels_a_pending_idle_timer(
    monkeypatch, _signal_state, _auto_signal_on
):
    monkeypatch.setenv("OC_SIGNAL_IDLE_HIDE", "30")
    _click()
    armed = S._STATE.signal_idle_timer

    assert S.signal_hide() == {"visible": False}  # contract unchanged
    assert S._STATE.signal_idle_timer is None
    assert S._STATE.signal_auto_shown is False
    armed.join(0.5)
    assert not armed.is_alive()


def test_idle_hide_fire_is_a_noop_once_the_overlay_is_gone(
    monkeypatch, _signal_state, _auto_signal_on
):
    """The timer thread may win the race with signal_hide — it must no-op."""
    monkeypatch.setenv("OC_SIGNAL_IDLE_HIDE", "30")
    _click()
    S.signal_hide()
    S._idle_hide_fire()  # must not raise, must not touch anything
    assert S._STATE.signal_indicator is None
    assert S._STATE.signal_auto_shown is False


def test_signal_status_reports_ownership_and_countdown(
    monkeypatch, _signal_state, _auto_signal_on
):
    monkeypatch.setenv("OC_SIGNAL_IDLE_HIDE", "30")
    _click()
    status = S.signal_status()
    assert status["auto_shown"] is True
    assert status["idle_hide_armed"] is True

    S.signal_show(mode="observe", agent="human")
    manual = S.signal_status()
    assert manual["auto_shown"] is False
    assert manual["idle_hide_armed"] is False


# ---------------------------------------------------------------------------
# Not-Aus / kill switch + pre-action grace period (Ticket T-20260818-895473048)
# ---------------------------------------------------------------------------

class _FakeReasonChannel:
    """Reason-dialog stand-in — accepts the ``reasons`` kwarg like TkAbortChannel."""

    answer: str | None = "Ich arbeite gerade selbst"

    def __init__(self, reasons=()):
        self.reasons = reasons

    def prompt_reason(self, *, context):
        return self.answer


def test_kill_switch_denies_do_even_under_allow_all(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    S._trigger_kill_switch("test reason")
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3})
    assert r["result"] == "aborted"
    assert r["abort_reason"] == "test reason"
    assert r["reason"] == "test reason"


def test_kill_switch_denies_click_name_and_invoke(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setattr(S, "_load_uia_feed", lambda *a, **k: _FakeUiaFeed())
    S._trigger_kill_switch("stop")
    assert S.click_name("Einfuegen")["result"] == "aborted"
    assert S.invoke("Einfuegen")["result"] == "aborted"


def test_kill_switch_flushes_a_running_batch_mid_flight(monkeypatch, _signal_state):
    """SOFORT: a batch already mid-flight must not run its remaining queued
    steps once the human hits abort — even though the whole batch is one
    Python call, the loop notices on its very next iteration."""
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")

    class _AbortingExec(_FakeExec):
        def execute(self, action):
            obs = super().execute(action)
            if len(self.executed) == 1:  # abort right after the 1st action ran
                S._trigger_kill_switch("mid-batch stop")
            return obs

    S._STATE.set_executor(_AbortingExec())
    r = S.do(actions=[
        {"type": "wait", "duration": 0.001},
        {"type": "wait", "duration": 0.001},
        {"type": "wait", "duration": 0.001},
    ])
    assert r["result"] == "aborted"
    assert r["executed_before"] == 1
    assert r["action_index"] == 1


def test_kill_switch_default_reason_when_none_given(_signal_state):
    S._trigger_kill_switch()
    r = S.do(action={"type": "mouse_move", "x": 0.1, "y": 0.1})
    assert r["result"] == "aborted"
    assert r["abort_reason"] == S._DEFAULT_ABORT_REASON


def test_capture_raises_when_kill_switch_latched(_signal_state):
    S._trigger_kill_switch("no screenshots now")
    with pytest.raises(PermissionError, match="no screenshots now"):
        S.capture()


def test_signal_show_resets_a_latched_kill_switch(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    S._trigger_kill_switch("stopped")
    assert S.do(action={"type": "left_click", "x": 0.5, "y": 0.3})["result"] == "aborted"

    monkeypatch.setenv("OC_SIGNAL_GRACE_SECONDS", "0")
    S.signal_show(mode="control", agent="kimi")  # fresh take-over re-arms it

    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3}, **_PRECLICK)
    assert r["result"] == "executed"


def test_signal_status_reports_aborted_state(_signal_state):
    S._trigger_kill_switch("stop please")
    status = S.signal_status()
    assert status["aborted"] is True
    assert status["abort_reason"] == "stop please"


def test_on_abort_latches_kill_switch_before_the_reason_dialog_resolves(
    monkeypatch, _signal_state
):
    """SOFORT means SOFORT: the switch must already be latched *while* the
    (potentially slow) reason dialog is still being answered."""
    seen: dict = {}

    class _ProbingChannel:
        def __init__(self, reasons=()):
            self.reasons = reasons

        def prompt_reason(self, *, context):
            seen["triggered_during_dialog"] = S._STATE.abort_triggered
            return "Ich arbeite gerade selbst"

    monkeypatch.setattr(S, "TkAbortChannel", _ProbingChannel, raising=False)
    S.signal_show(mode="control", agent="kimi")
    on_abort = S._STATE.signal_indicator.renderer.kwargs["on_abort"]

    on_abort()

    assert seen["triggered_during_dialog"] is True
    assert S._STATE.abort_reason == "Ich arbeite gerade selbst"


def test_on_abort_passes_configured_quick_reasons_to_the_channel(
    monkeypatch, _signal_state, tmp_path
):
    from open_compute.indicator import SignalConfig

    captured: dict = {}

    class _CapturingChannel:
        def __init__(self, reasons=()):
            captured["reasons"] = reasons

        def prompt_reason(self, *, context):
            return None

    monkeypatch.setattr(S, "TkAbortChannel", _CapturingChannel, raising=False)
    cfg = SignalConfig.from_dict({"abort_reasons": ["Falsches Fenster", "Spaeter erneut"]})
    path = tmp_path / "cfg.json"
    cfg.save(path)

    S.signal_show(mode="control", agent="kimi", config_path=str(path))
    on_abort = S._STATE.signal_indicator.renderer.kwargs["on_abort"]
    on_abort()

    assert captured["reasons"] == ("Falsches Fenster", "Spaeter erneut")


def test_rec_replay_reports_abort_reason(monkeypatch, _signal_state):
    """Simulates the real sequence: the grace/kill-switch check at the top of
    `rec_replay` passes (nothing latched yet), the replay starts, and only
    THEN — mid-replay — does the human hit abort; `_GatedExecutor` notices on
    the next step and raises, which `rec_replay` must translate back into
    `abort_reason` for the caller."""
    import open_compute.cli as oc_cli

    def _raising_run_replay(path, params, executor, policy=None, abort_check=None):
        S._trigger_kill_switch("stop it")  # the abort happens mid-replay
        raise PermissionError("aborted: stop it")

    monkeypatch.setattr(oc_cli, "_run_replay", _raising_run_replay)
    r = S.rec_replay("dummy.clirec")
    assert r["result"] == "deny"
    assert r["abort_reason"] == "stop it"


def test_rec_replay_plain_deny_has_no_abort_reason(monkeypatch, _signal_state):
    """A regular safety-gate deny (no abort involved) must not gain the new field."""
    import open_compute.cli as oc_cli

    def _raising_run_replay(path, params, executor, policy=None, abort_check=None):
        raise PermissionError("safety gate: deny for replay action 'type'")

    monkeypatch.setattr(oc_cli, "_run_replay", _raising_run_replay)
    r = S.rec_replay("dummy.clirec")
    assert r["result"] == "deny"
    assert "abort_reason" not in r


def test_gated_executor_checks_abort_before_the_safety_policy():
    """cli._GatedExecutor: an abort_check hit must pre-empt even allow_all —
    the Not-Aus is not just another safety-policy rule an operator can loosen."""
    from open_compute.actions import Action, ActionType
    from open_compute.cli import _GatedExecutor
    from open_compute.safety import SafetyPolicy

    gated = _GatedExecutor(
        _FakeExec(), SafetyPolicy(mode="allow_all"),
        abort_check=lambda: "human said stop",
    )
    with pytest.raises(PermissionError, match="aborted: human said stop"):
        gated.execute(Action(type=ActionType.LEFT_CLICK, x=0.1, y=0.1))


def test_gated_executor_without_abort_check_behaves_as_before():
    from open_compute.actions import Action, ActionType
    from open_compute.cli import _GatedExecutor
    from open_compute.safety import SafetyPolicy

    fake = _FakeExec()
    gated = _GatedExecutor(fake, SafetyPolicy(mode="allow_all"))
    gated.execute(Action(type=ActionType.LEFT_CLICK, x=0.1, y=0.1))
    assert len(fake.executed) == 1


# --- pre-action grace period --------------------------------------------

def test_grace_period_blocks_the_first_action_until_it_elapses(
    monkeypatch, _signal_state
):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_GRACE_SECONDS", "0.15")
    S.signal_show(mode="control", agent="kimi")

    start = time.monotonic()
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3}, **_PRECLICK)
    elapsed = time.monotonic() - start

    assert r["result"] == "executed"
    assert elapsed >= 0.15


def test_grace_period_zero_means_no_wait(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_GRACE_SECONDS", "0")
    S.signal_show(mode="control", agent="kimi")

    start = time.monotonic()
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3}, **_PRECLICK)
    elapsed = time.monotonic() - start

    assert r["result"] == "executed"
    assert elapsed < 1.0


def test_grace_period_short_circuits_if_already_aborted(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SIGNAL_GRACE_SECONDS", "10")
    S.signal_show(mode="control", agent="kimi")
    S._trigger_kill_switch("already stopped")

    start = time.monotonic()
    r = S.do(action={"type": "mouse_move", "x": 0.1, "y": 0.1})
    elapsed = time.monotonic() - start

    assert r["result"] == "aborted"
    assert elapsed < 1.0  # must not wait out the 10 s grace first


def test_grace_period_aborts_mid_wait_without_waiting_out_the_window(
    monkeypatch, _signal_state
):
    monkeypatch.setenv("OC_SIGNAL_GRACE_SECONDS", "5")
    S.signal_show(mode="control", agent="kimi")

    def _abort_soon() -> None:
        time.sleep(0.1)
        S._trigger_kill_switch("aborted mid countdown")

    threading.Thread(target=_abort_soon, daemon=True).start()
    start = time.monotonic()
    r = S.do(action={"type": "mouse_move", "x": 0.1, "y": 0.1})
    elapsed = time.monotonic() - start

    assert r["result"] == "aborted"
    assert elapsed < 5.0 - 1.0  # nowhere near the full 5 s window


def test_capture_blocks_during_grace_then_succeeds(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SIGNAL_GRACE_SECONDS", "0.1")
    S.signal_show(mode="control", agent="kimi")

    start = time.monotonic()
    result = S.capture()
    elapsed = time.monotonic() - start

    assert any(isinstance(item, ImageContent) for item in result.content)
    assert elapsed >= 0.1


def test_auto_signal_kept_across_calls_does_not_arm_a_grace_period(
    monkeypatch, _signal_state
):
    """An auto signal may be explicitly leased across calls without a new wait."""
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "control")
    _click()
    assert S._STATE.signal_indicator is not None
    assert S._STATE.grace_deadline is None

    start = time.monotonic()
    r = _click()
    elapsed = time.monotonic() - start
    assert r["result"] == "executed"
    assert elapsed < 1.0


# --- User-Aktivitaets-Wache (opt-in, OC_HUMAN_ACTIVITY_WATCH) -----------

def _fake_activity(*, recent: bool, provenance: str) -> None:
    """Inject a deterministic human_activity classifier/adapter pair."""
    from open_compute import human_activity as ha

    class _FixedAdapter:
        def sample(self):
            return ha.LastInputSample(observed_tick_ms=1000, last_input_tick_ms=1000)

    class _FixedClassifier:
        def assess(self, sample):
            return ha.ActivityAssessment(
                recent=recent,
                provenance=ha.InputProvenance(provenance),
                age_ms=0,
                device="unknown",
            )

    S._STATE.activity_classifier = _FixedClassifier()
    S._STATE.activity_adapter = _FixedAdapter()


def test_activity_watch_off_by_default_ignores_real_human_input(
    monkeypatch, _signal_state
):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    _fake_activity(recent=True, provenance="human")
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3}, **_PRECLICK)
    assert r["result"] == "executed"  # opt-in feature, unset env => no-op


def test_activity_watch_pauses_on_real_recent_human_input(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_HUMAN_ACTIVITY_WATCH", "on")
    monkeypatch.setattr(S, "_activity_watch_active", lambda: True)
    _fake_activity(recent=True, provenance="human")

    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3})
    assert r["result"] == "aborted"
    assert "Nutzer-Eingabe" in r["abort_reason"]
    # latched, not one-shot — every further action needs a fresh signal_show
    assert S._STATE.abort_triggered is True


def test_activity_watch_lets_agent_owned_input_through(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_HUMAN_ACTIVITY_WATCH", "on")
    monkeypatch.setattr(S, "_activity_watch_active", lambda: True)
    _fake_activity(recent=True, provenance="agent")

    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3}, **_PRECLICK)
    assert r["result"] == "executed"


def test_activity_watch_lets_stale_input_through(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_HUMAN_ACTIVITY_WATCH", "on")
    monkeypatch.setattr(S, "_activity_watch_active", lambda: True)
    _fake_activity(recent=False, provenance="unknown")

    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3}, **_PRECLICK)
    assert r["result"] == "executed"


def test_activity_watch_enabled_env_values(monkeypatch):
    for value in ("1", "true", "TRUE", "on", "yes"):
        monkeypatch.setenv("OC_HUMAN_ACTIVITY_WATCH", value)
        assert S._activity_watch_enabled() is True
    for value in ("", "0", "false", "off"):
        monkeypatch.setenv("OC_HUMAN_ACTIVITY_WATCH", value)
        assert S._activity_watch_enabled() is False
