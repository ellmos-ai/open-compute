from open_compute.clirec.capture.base import RawEvent
from open_compute.clirec.capture.mock import MockCaptureBackend
from open_compute.clirec.recorder import Recorder, RecorderConfig


class Clock:
    def __init__(self): self.t = 0.0
    def __call__(self): return self.t


def test_start_pump_stop_produces_recording(tmp_path):
    evts = [RawEvent("mouse_down", 0.0, x=5, y=6, button="left"),
            RawEvent("mouse_up", 0.0, x=5, y=6, button="left")]
    be = MockCaptureBackend(evts)
    rec = Recorder(be, config=RecorderConfig(recordings_dir=str(tmp_path)),
                   host="LAPTOP", resolution="1920x1080")
    rec.start("demo")
    rec.pump()
    out = rec.stop()
    assert out.title == "demo" and out.resolution == "1920x1080"
    assert len(out.steps) == 1 and out.steps[0].action == "click"


def test_save_writes_file(tmp_path):
    be = MockCaptureBackend([RawEvent("char", 0.0, char="x")])
    rec = Recorder(be, config=RecorderConfig(recordings_dir=str(tmp_path)))
    rec.start("t"); rec.pump()
    out = rec.stop()
    path = rec.save(out, "myflow")
    assert path.endswith("myflow.clirec")
    import os
    assert os.path.exists(path)


def test_ringbuffer_cut_last_keeps_recent(tmp_path):
    clk = Clock()
    be = MockCaptureBackend([])
    rec = Recorder(be, config=RecorderConfig(ringbuffer_enabled=True, ringbuffer_minutes=1,
                                             recordings_dir=str(tmp_path)), clock=clk)
    rec.start("buf")
    # inject an old char then a new char via two pumps with advancing clock
    be._events = [RawEvent("char", clk.t, char="old")]
    rec.pump()
    clk.t = 120.0  # 2 minutes later
    be._events = [RawEvent("char", clk.t, char="new")]
    rec.pump()
    out = rec.cut_last(1.0, "recent")  # keep last 1 minute
    joined = "".join(s.text or "" for s in out.steps)
    assert "new" in joined and "old" not in joined


def test_set_paused_delegates():
    be = MockCaptureBackend([RawEvent("char", 0.0, char="a")])
    rec = Recorder(be, config=RecorderConfig())
    rec.start("p")
    rec.set_paused(True)
    rec.pump()
    out = rec.stop()
    assert out.steps == []


def test_stop_resets_buffer(tmp_path):
    be = MockCaptureBackend([RawEvent("char", 0.0, char="a")])
    rec = Recorder(be, config=RecorderConfig(recordings_dir=str(tmp_path)))
    rec.start("r")
    rec.pump()
    rec.stop()
    # Second stop without start/pump should yield empty steps
    out = rec.stop()
    assert out.steps == []
