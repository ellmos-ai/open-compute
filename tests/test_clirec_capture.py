# tests/test_clirec_capture.py
from open_compute.clirec.capture import base
from open_compute.clirec.capture.mock import MockCaptureBackend


def test_mock_backend_polls_events_once():
    evts = [base.RawEvent("mouse_down", 0.0, x=10, y=20, button="left"),
            base.RawEvent("mouse_up", 0.1, x=10, y=20, button="left")]
    be = MockCaptureBackend(evts)
    be.start()
    first = be.poll()
    assert len(first) == 2 and first[0].kind == "mouse_down"
    assert be.poll() == []  # drained
    be.stop()


def test_mock_backend_respects_pause():
    evts = [base.RawEvent("key_down", 0.0, key="a")]
    be = MockCaptureBackend(evts)
    be.start()
    be.set_paused(True)
    assert be.poll() == []      # paused: nothing surfaces
    be.set_paused(False)
    assert len(be.poll()) == 1  # resumes


def test_get_backend_unknown_raises():
    import pytest
    with pytest.raises(RuntimeError):
        base.get_backend("does-not-exist")
