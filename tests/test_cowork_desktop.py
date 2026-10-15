"""Desktop guard tests use injected Win32/driver boundaries; no OS input."""
import threading

import pytest

from open_compute.actions import Action, ActionType
from open_compute.cowork_desktop import DesktopBackend
from open_compute.interaction import send_text_verified
from test_cowork import FakeDesktop
from open_compute.cowork_desktop import activate_verified


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
