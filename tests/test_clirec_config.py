from open_compute.config import Config
from open_compute.clirec.recorder import RecorderConfig


def test_config_has_clirec_defaults():
    cfg = Config()
    assert cfg.clirec["ringbuffer_enabled"] is False
    assert cfg.clirec["ringbuffer_minutes"] == 15
    assert cfg.clirec["recordings_dir"] == "recordings"


def test_config_loads_clirec_override_from_dict():
    cfg = Config.from_dict({"clirec": {"ringbuffer_enabled": True, "ringbuffer_minutes": 30}})
    assert cfg.clirec["ringbuffer_enabled"] is True
    assert cfg.clirec["ringbuffer_minutes"] == 30
    # untouched keys keep defaults
    assert cfg.clirec["recordings_dir"] == "recordings"


def test_clirec_recorder_config_mapping():
    from open_compute.config import clirec_recorder_config
    cfg = Config.from_dict({"clirec": {"ringbuffer_enabled": True}})
    rc = clirec_recorder_config(cfg)
    assert isinstance(rc, RecorderConfig)
    assert rc.ringbuffer_enabled is True and rc.ringbuffer_minutes == 15
