"""Tests for the FastMCP server wrapper (open_compute.mcp_server).

Mock-only: a tiny in-process fake executor is injected via ``_STATE.set_executor``
so the whole suite runs on any platform without mss / a real desktop. The MCP
SDK is an optional extra, so the module is import-or-skipped.
"""

import asyncio
import time

import pytest

pytest.importorskip("mcp")  # server needs the optional open-compute[mcp] extra

from mcp.server.fastmcp import Image  # noqa: E402

from open_compute import mcp_server as S  # noqa: E402


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


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.delenv("OC_SAFETY_MODE", raising=False)
    monkeypatch.delenv("OC_DENY", raising=False)
    S._STATE.set_executor(_FakeExec())
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
    assert {"action", "actions", "mode"} <= set(props)


def test_capture_returns_image():
    img = S.capture()
    assert isinstance(img, Image)
    assert img.data  # PNG bytes passed through


def test_click_confirm_gates_by_default():
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3})
    assert r["result"] == "needs_confirmation"
    assert r["action"] == "left_click"


def test_click_executes_when_server_allows(monkeypatch):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3})
    assert r["result"] == "executed"
    assert r["action"] == "left_click"


def test_non_risky_action_allowed_by_default():
    r = S.do(action={"type": "mouse_move", "x": 0.5, "y": 0.5})
    assert r["result"] == "executed"


def test_read_only_denies_state_change():
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3}, mode="read_only")
    assert r["result"] == "deny"


def test_action_alias_key_accepted():
    # 'action' as an alias for 'type' (Claude-style dicts)
    r = S.do(action={"action": "mouse_move", "x": 0.4, "y": 0.4})
    assert r["result"] == "executed"


def test_batch_executes_in_order(monkeypatch):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    r = S.do(
        actions=[{"type": "mouse_move", "x": 0.1, "y": 0.1},
                 {"type": "left_click", "x": 0.2, "y": 0.2}],
    )
    assert r["result"] == "batch"
    assert r["count"] == 2


def test_batch_stops_at_gate():
    r = S.do(
        actions=[{"type": "mouse_move", "x": 0.1, "y": 0.1},
                 {"type": "left_click", "x": 0.2, "y": 0.2}],
    )  # default confirm: mouse_move ok, left_click needs confirmation
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
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3})
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
    S._STATE.signal_indicator = None
    S._STATE.signal_mode = ""
    S._STATE.pending_abort_message = None
    S._STATE.signal_auto_shown = False


@pytest.fixture
def _signal_state(monkeypatch):
    """Reset signal state and inject a fake overlay for every test here."""
    monkeypatch.setattr(S, "WindowsBorderOverlay", _FakeOverlay, raising=False)
    _reset_signal_state()
    yield
    _reset_signal_state()


def test_signal_show_and_hide(_signal_state):
    result = S.signal_show(mode="control", agent="kimi")
    assert result["visible"] is True
    assert result["mode"] == "control"
    assert result["color"] == [255, 40, 60]
    assert "kimi" in result["label"]

    status = S.signal_status()
    assert status["visible"] is True
    assert status["mode"] == "control"
    assert status["pending_abort_message"] is None

    hidden = S.signal_hide()
    assert hidden == {"visible": False}
    assert S.signal_status()["visible"] is False


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

    def invoke(self, query, window=None):
        self.invoked.append((query, window))
        return self.invoke_result


@pytest.fixture(autouse=True)
def _clean_signal_auto_env(monkeypatch):
    """OC_SIGNAL_AUTO must not leak in from (or out to) the real environment."""
    monkeypatch.delenv("OC_SIGNAL_AUTO", raising=False)
    monkeypatch.delenv("OC_SIGNAL_IDLE_HIDE", raising=False)
    yield


def test_auto_signal_off_by_default(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3})
    assert r["result"] == "executed"
    assert "auto_signal_error" not in r
    assert S._STATE.signal_indicator is None


def test_auto_signal_off_value_disables(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "off")
    S.do(action={"type": "left_click", "x": 0.5, "y": 0.3})
    assert S._STATE.signal_indicator is None


def test_auto_signal_shows_after_executed_action(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "control")
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3})
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
    S.signal_show(mode="observe", agent="human")
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "control")
    S.do(action={"type": "left_click", "x": 0.5, "y": 0.3})
    # a manually shown signal (any mode) is never overridden by auto-signal
    assert S._STATE.signal_mode == "observe"


def test_auto_signal_invalid_mode_reports_error_without_crashing(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "not-a-mode")
    r = S.do(action={"type": "left_click", "x": 0.5, "y": 0.3})
    # the action itself must still succeed — a bad auto-signal config must
    # never crash or block the tool it is attached to
    assert r["result"] == "executed"
    assert "not-a-mode" in r["auto_signal_error"]
    assert S._STATE.signal_indicator is None


def test_auto_signal_fires_once_for_a_batch(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "control")
    r = S.do(actions=[
        {"type": "mouse_move", "x": 0.1, "y": 0.1},
        {"type": "left_click", "x": 0.2, "y": 0.2},
    ])
    assert r["result"] == "batch"
    assert S._STATE.signal_mode == "control"
    # only one overlay call — the second gate pass sees signal already visible
    assert len(S._STATE.signal_indicator.renderer.shown) == 1


def test_auto_signal_click_name(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "control")
    monkeypatch.setattr(S, "_load_uia_feed", lambda *a, **k: _FakeUiaFeed())
    r = S.click_name("Einfuegen")
    assert r["result"] == "executed"
    assert S._STATE.signal_mode == "control"


def test_auto_signal_invoke(monkeypatch, _signal_state):
    monkeypatch.setenv("OC_SAFETY_MODE", "allow_all")
    monkeypatch.setenv("OC_SIGNAL_AUTO", "control")
    monkeypatch.setattr(S, "_load_uia_feed", lambda *a, **k: _FakeUiaFeed())
    r = S.invoke("Einfuegen")
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
        lambda path, params, executor, policy=None: {"steps": 1},
    )
    r = S.rec_replay("dummy.clirec")
    assert r["result"] == "replayed"
    assert S._STATE.signal_mode == "control"


# ---------------------------------------------------------------------------
# OC_SIGNAL_IDLE_HIDE — take the auto-shown overlay down once steering stops
# ---------------------------------------------------------------------------

def _click(**_kw):
    return S.do(action={"type": "left_click", "x": 0.5, "y": 0.3})


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

    S.click_name("Einfuegen")
    after_click = S._STATE.signal_idle_timer
    assert after_click is not None and after_click is not after_do

    S.invoke("Einfuegen")
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
