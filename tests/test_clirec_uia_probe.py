"""Test: optional UIA probe (DefaultProbe) with graceful degradation."""
from open_compute.clirec.uia_probe import DefaultProbe


def test_probe_degrades_without_uia(monkeypatch):
    p = DefaultProbe()
    # Force the "unavailable" path regardless of host:
    monkeypatch.setattr(p, "_load", lambda: None)
    assert p.available() is False
    assert p.element_at(10, 10) is None
    assert p.is_password_focused() is False


def test_probe_element_at_uses_loaded_backend(monkeypatch):
    p = DefaultProbe()

    class FakeUia:
        def element_from_point(self, x, y):
            return {"name": "OK", "window": "Dlg", "role": "button"}
        def focused_is_password(self):
            return True

    monkeypatch.setattr(p, "_load", lambda: FakeUia())
    assert p.available() is True
    assert p.element_at(1, 2)["name"] == "OK"
    assert p.is_password_focused() is True
