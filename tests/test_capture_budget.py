"""Capture-budget knobs: shrink what the model is billed for, keep control exact."""

from __future__ import annotations

import io

import pytest

pytest.importorskip("mcp", reason="server needs the optional open-compute[mcp] extra")
PIL = pytest.importorskip("PIL")

from open_compute import mcp_server  # noqa: E402
from PIL import Image as PILImage  # noqa: E402


def _png(width: int = 1920, height: int = 1080, colour: str = "red") -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (width, height), colour).save(buf, format="PNG")
    return buf.getvalue()


def _size(png: bytes) -> tuple[int, int]:
    with PILImage.open(io.BytesIO(png)) as img:
        return img.size


def _mode(png: bytes) -> str:
    with PILImage.open(io.BytesIO(png)) as img:
        return img.mode


def test_off_by_default_returns_identical_bytes(monkeypatch):
    monkeypatch.delenv("OC_CAPTURE_SCALE", raising=False)
    monkeypatch.delenv("OC_CAPTURE_MAX_DIM", raising=False)
    monkeypatch.delenv("OC_CAPTURE_GRAYSCALE", raising=False)
    original = _png()
    assert mcp_server._shrink_png(original) is original


def test_scale_halves_both_edges(monkeypatch):
    monkeypatch.setenv("OC_CAPTURE_SCALE", "0.5")
    assert _size(mcp_server._shrink_png(_png())) == (960, 540)


def test_max_dim_caps_the_longest_edge_and_keeps_aspect(monkeypatch):
    monkeypatch.setenv("OC_CAPTURE_MAX_DIM", "768")
    width, height = _size(mcp_server._shrink_png(_png()))
    assert width == 768
    assert height == 432  # 16:9 preserved


def test_scale_and_max_dim_compose(monkeypatch):
    monkeypatch.setenv("OC_CAPTURE_SCALE", "0.5")  # 1920 -> 960
    monkeypatch.setenv("OC_CAPTURE_MAX_DIM", "480")  # 960 -> 480
    assert _size(mcp_server._shrink_png(_png())) == (480, 270)


def test_grayscale_converts_mode(monkeypatch):
    monkeypatch.setenv("OC_CAPTURE_GRAYSCALE", "1")
    assert _mode(mcp_server._shrink_png(_png())) == "L"


@pytest.mark.parametrize("bad", ["0", "-1", "1.5", "abc", ""])
def test_invalid_scale_falls_back_to_off(monkeypatch, bad):
    monkeypatch.setenv("OC_CAPTURE_SCALE", bad)
    assert mcp_server._capture_budget()[0] == 1.0


def test_invalid_max_dim_falls_back_to_off(monkeypatch):
    monkeypatch.setenv("OC_CAPTURE_MAX_DIM", "not-a-number")
    assert mcp_server._capture_budget()[1] == 0


def test_corrupt_png_is_returned_unchanged(monkeypatch):
    """A cosmetic shrink must never turn a usable capture into an exception."""
    monkeypatch.setenv("OC_CAPTURE_SCALE", "0.5")
    junk = b"not a png at all"
    assert mcp_server._shrink_png(junk) == junk


def test_shrunk_png_is_smaller_on_disk(monkeypatch):
    monkeypatch.setenv("OC_CAPTURE_SCALE", "0.4")
    original = _png(colour="blue")
    assert len(mcp_server._shrink_png(original)) < len(original)
