"""Tests for coordinate normalization / denormalization."""

from __future__ import annotations

import pytest

from open_compute.coordinates import denormalize, normalize, rescale


def test_normalize_center():
    nx, ny = normalize(640, 400, 1280, 800)
    assert nx == pytest.approx(0.5)
    assert ny == pytest.approx(0.5)


def test_normalize_clamps_out_of_range():
    nx, ny = normalize(2000, -10, 1280, 800)
    assert nx == 1.0
    assert ny == 0.0


def test_denormalize_center():
    px, py = denormalize(0.5, 0.5, 1280, 800)
    assert px == 640
    assert py == 400


def test_denormalize_stays_in_bounds():
    px, py = denormalize(1.0, 1.0, 1280, 800)
    assert px == 1279
    assert py == 799


def test_round_trip_is_stable():
    for px, py in [(0, 0), (123, 456), (1279, 799)]:
        nx, ny = normalize(px, py, 1280, 800)
        rx, ry = denormalize(nx, ny, 1280, 800)
        assert abs(rx - px) <= 1
        assert abs(ry - py) <= 1


def test_rescale_between_resolutions():
    # Center of a 1920x1080 capture maps to center of a 1280x800 display.
    px, py = rescale(960, 540, (1920, 1080), (1280, 800))
    assert px == 640
    assert py == 400


def test_invalid_dimensions_raise():
    with pytest.raises(ValueError):
        normalize(1, 1, 0, 100)
    with pytest.raises(ValueError):
        denormalize(0.5, 0.5, 100, 0)
