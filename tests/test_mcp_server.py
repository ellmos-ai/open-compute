"""Tests for the FastMCP server wrapper (open_compute.mcp_server).

Mock-only: a tiny in-process fake executor is injected via ``_STATE.set_executor``
so the whole suite runs on any platform without mss / a real desktop. The MCP
SDK is an optional extra, so the module is import-or-skipped.
"""

import asyncio

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


@pytest.fixture
def _signal_state(monkeypatch):
    """Reset signal state and inject a fake overlay for every test here."""
    monkeypatch.setattr(S, "WindowsBorderOverlay", _FakeOverlay, raising=False)
    S._STATE.signal_indicator = None
    S._STATE.signal_mode = ""
    S._STATE.pending_abort_message = None
    yield
    S._STATE.signal_indicator = None
    S._STATE.signal_mode = ""
    S._STATE.pending_abort_message = None


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
