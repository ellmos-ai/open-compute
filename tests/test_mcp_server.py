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
        "capture", "click_name", "do", "invoke",
        "push_status", "rec_replay", "tree", "watch_dir",
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
