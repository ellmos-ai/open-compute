import platform
from open_compute.clirec.capture.base import get_backend, CaptureBackend


def test_get_backend_auto_returns_capturebackend():
    be = get_backend()  # winapi on Windows, pynput otherwise
    assert isinstance(be, CaptureBackend)
    assert hasattr(be, "available")


def test_backend_available_is_bool():
    be = get_backend()
    assert isinstance(be.available(), bool)
