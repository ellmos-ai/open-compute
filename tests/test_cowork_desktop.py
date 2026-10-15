"""Desktop guard tests use injected Win32/driver boundaries; no OS input."""
import threading

import pytest

from open_compute.actions import Action, ActionType
from open_compute.cowork_desktop import DesktopBackend
from open_compute.interaction import send_text_verified
from test_cowork import FakeDesktop
from open_compute.cowork_desktop import activate_verified
from contextlib import nullcontext
from types import SimpleNamespace


def test_segmented_text_reports_partial_on_cancel(monkeypatch):
    fake = FakeDesktop()
    backend = DesktopBackend()
    cancel = threading.Event()
    sent = []
    class Executor:
        coordinate_frame = (0, 0, 800, 600)
        def activate_window_identity(self, target):
            pass
        def type_text_verified(self, text, check_focus, expected_window, chunk_chars):
            def send(chunk):
                sent.append(chunk)
                cancel.set()
                return len(chunk)
            return send_text_verified(text, send_chunk=send, check_focus=check_focus,
                                      expected_window=expected_window, chunk_chars=chunk_chars)
    backend._executor = Executor()
    monkeypatch.setattr(backend, "_activate_target", backend._executor.activate_window_identity)
    backend.tokens[fake.window["window_token"]] = fake.window
    monkeypatch.setattr(backend, "windows", fake.windows)
    monkeypatch.setattr(backend, "_semantics", lambda target: fake.payload)
    class Monitor:
        def __init__(self, cancel): pass
        def start(self): pass
        def stop(self): pass
    monkeypatch.setattr("open_compute.cowork_desktop.PhysicalInputMonitor", Monitor)
    obs = backend.observe(fake.window)
    result = backend.execute(Action(ActionType.TYPE, text="abcdefghijklmnop"), fake.window, obs, cancel)
    assert sent == ["abcdefgh"]
    assert result["status"] == "uncertain"
    assert result["text_transfer"]["sent_chars"] == 8
    assert result["text_transfer"]["partial"] is True


def test_post_activation_changed_content_blocks_before_input(monkeypatch):
    fake = FakeDesktop()
    backend = DesktopBackend()
    class Executor:
        coordinate_frame = (0, 0, 800, 600)
        def activate_window_identity(self, target):
            fake.payload = {"elements": [{"name": "changed after activation"}]}
        def execute(self, action):
            pytest.fail("must never dispatch")
    backend._executor = Executor()
    monkeypatch.setattr(backend, "_activate_target", backend._executor.activate_window_identity)
    backend.tokens[fake.window["window_token"]] = fake.window
    monkeypatch.setattr(backend, "windows", fake.windows)
    monkeypatch.setattr(backend, "_semantics", lambda target: fake.payload)
    class Monitor:
        def __init__(self, cancel): pass
        def start(self): pass
        def stop(self): pass
    monkeypatch.setattr("open_compute.cowork_desktop.PhysicalInputMonitor", Monitor)
    obs = backend.observe(fake.window)
    with pytest.raises(PermissionError, match="target state"):
        backend.execute(Action(ActionType.KEY, text="Return"), fake.window, obs, threading.Event())


@pytest.mark.parametrize("successful", [True, False])
def test_activation_uses_readback_and_always_detaches(successful):
    class User:
        foreground = 8
        attaches = []
        def GetForegroundWindow(self): return self.foreground
        def GetWindowThreadProcessId(self, hwnd, pid): return hwnd
        def AttachThreadInput(self, source, target, attached):
            self.attaches.append((target, attached))
            return True
        def IsIconic(self, hwnd): return False
        def BringWindowToTop(self, hwnd): pass
        def SetForegroundWindow(self, hwnd):
            if successful:
                self.foreground = hwnd
            return 0  # zero return must not override actual foreground proof
    class Kernel:
        def GetCurrentThreadId(self): return 1
    user = User()
    if successful:
        activate_verified(42, user32=user, kernel32=Kernel())
    else:
        with pytest.raises(PermissionError):
            activate_verified(42, user32=user, kernel32=Kernel())
    assert user.attaches == [(8, True), (42, True), (42, False), (8, False)]


@pytest.mark.parametrize("text,password", [("hallo welt", False), ("x" * 351, False), ("SECRET", True)])
def test_selected_editor_content_is_bounded_and_password_never_accessed(monkeypatch, text, password):
    backend = DesktopBackend()
    backend._executor = SimpleNamespace(coordinate_frame=(0, 0, 800, 600))
    class Editor:
        IsPassword = password
        HasKeyboardFocus = False  # a single chosen editor also works behind the UI
        ControlTypeName = "EditControl"
        BoundingRectangle = SimpleNamespace(left=10, top=10, right=410, bottom=210)
        @property
        def Name(self):
            assert not password
            return ""
        def GetTextPattern(self):
            assert not password
            return None
        def GetValuePattern(self):
            assert not password
            return SimpleNamespace(Value=text)
    uia = SimpleNamespace(UIAutomationInitializerInThread=nullcontext,
        ControlFromHandle=lambda hwnd: object(), WalkControl=lambda root, maxDepth: [(Editor(), 0)])
    monkeypatch.setattr("open_compute.feeds.uia_windows._get_uia", lambda: uia)
    packet = backend._semantics(FakeDesktop().window)
    assert len(__import__("json").dumps(packet, ensure_ascii=False, separators=(",", ":"))) <= 1200
    if password:
        assert "SECRET" not in str(packet) and packet["content_readable"] is False
    else:
        assert packet["content_readable"] is True
        assert packet["content_truncated"] is (len(text) > 350)
        assert any(element.get("value") == text[:350] for element in packet["elements"])
