"""Tests for backend dispatch via the factory and the mock backend."""

from __future__ import annotations

import pytest

from open_compute.actions import Action, ActionType
from open_compute.backends import BackendResult, MockBackend, get_backend
from open_compute.backends.base import ComputerBackend
from open_compute.perception import Observation


def _obs() -> Observation:
    return Observation(screenshot=b"x", width=1280, height=800)


def test_factory_returns_mock():
    backend = get_backend("mock", 1280, 800)
    assert isinstance(backend, ComputerBackend)
    assert backend.name == "mock"


def test_factory_unknown_backend_raises():
    with pytest.raises(ValueError):
        get_backend("nope", 1280, 800)


def test_mock_backend_default_script_finishes():
    backend = MockBackend()
    r1 = backend.start("do a thing", _obs())
    assert r1.actions and r1.actions[0].type is ActionType.SCREENSHOT
    backend.step(_obs())  # click
    r3 = backend.step(_obs())
    assert r3.done is True


def test_mock_backend_custom_script():
    script = [
        BackendResult(actions=[Action(ActionType.TYPE, text="hello")]),
        BackendResult(done=True),
    ]
    backend = MockBackend(script=script)
    first = backend.start("type hello", _obs())
    assert first.actions[0].text == "hello"
    assert backend.step(_obs()).done is True


def test_claude_factory_without_sdk_raises_importerror():
    # The 'anthropic' SDK is an optional extra and not installed in CI.
    # get_backend must surface a clear ImportError when constructing it.
    pytest.importorskip  # marker; we expect the import to fail below
    try:
        import anthropic  # noqa: F401

        has_sdk = True
    except ImportError:
        has_sdk = False

    if has_sdk:
        pytest.skip("anthropic SDK is installed; ImportError path not applicable")
    with pytest.raises(ImportError):
        get_backend("claude", 1280, 800)


def test_claude_backend_with_injected_client(monkeypatch):
    """A fake client lets us test the Claude backend without the SDK."""
    from open_compute.backends.claude import ClaudeComputerBackend

    class _Block:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class _Response:
        def __init__(self):
            self.content = [
                _Block(type="text", text="clicking"),
                _Block(
                    type="tool_use",
                    id="tu_1",
                    input={"action": "left_click", "coordinate": [640, 400]},
                ),
            ]
            self.stop_reason = "tool_use"

    class _Messages:
        def create(self, **kwargs):
            assert kwargs["betas"] == ["computer-use-2025-11-24"]
            assert kwargs["tools"][0]["type"] == "computer_20251124"
            return _Response()

    class _Beta:
        messages = _Messages()

    class _FakeClient:
        beta = _Beta()

    backend = ClaudeComputerBackend(1280, 800, client=_FakeClient())
    result = backend.start("click center", _obs())
    assert len(result.actions) == 1
    action = result.actions[0]
    assert action.type is ActionType.LEFT_CLICK
    assert action.x == pytest.approx(0.5)
    assert action.y == pytest.approx(0.5)
    assert result.done is False  # stop_reason == tool_use
