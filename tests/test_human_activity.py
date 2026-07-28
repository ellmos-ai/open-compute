from __future__ import annotations

import pytest

from open_compute.human_activity import (
    GetLastInputInfoAdapter,
    HumanActivityClassifier,
    InputProvenance,
    LastInputSample,
)


def test_adapter_uses_injected_queries_without_native_api() -> None:
    calls: list[str] = []
    adapter = GetLastInputInfoAdapter(
        query_last_input_ms=lambda: calls.append("last") or 9_950,
        query_tick_count_ms=lambda: calls.append("now") or 10_000,
    )

    sample = adapter.sample()

    assert sample.age_ms == 50
    assert calls == ["now", "last"]


def test_adapter_requires_both_injected_queries() -> None:
    with pytest.raises(ValueError, match="both"):
        GetLastInputInfoAdapter(query_last_input_ms=lambda: 1)


def test_tick_age_handles_dword_wraparound() -> None:
    sample = LastInputSample(
        observed_tick_ms=15,
        last_input_tick_ms=(2**32) - 10,
    )

    assert sample.age_ms == 25


def test_recent_unmatched_input_is_human_without_key_data() -> None:
    classifier = HumanActivityClassifier(recent_threshold_ms=500)

    result = classifier.assess(
        LastInputSample(
            observed_tick_ms=10_000,
            last_input_tick_ms=9_750,
            device="keyboard",
        )
    )

    assert result.recent is True
    assert result.provenance is InputProvenance.HUMAN
    assert result.device == "keyboard"
    assert set(result.__dict__) == {
        "recent",
        "provenance",
        "age_ms",
        "device",
        "action_id",
    }


def test_own_injected_window_is_not_classified_as_human() -> None:
    classifier = HumanActivityClassifier(
        recent_threshold_ms=500,
        injection_tolerance_ms=20,
    )
    classifier.record_agent_input("action-7", 9_700, 9_800)

    result = classifier.assess(
        LastInputSample(observed_tick_ms=10_000, last_input_tick_ms=9_805)
    )

    assert result.recent is True
    assert result.provenance is InputProvenance.AGENT
    assert result.action_id == "action-7"


def test_old_input_is_unknown_not_a_human_interrupt() -> None:
    classifier = HumanActivityClassifier(recent_threshold_ms=250)

    result = classifier.assess(
        LastInputSample(observed_tick_ms=10_000, last_input_tick_ms=9_000)
    )

    assert result.recent is False
    assert result.provenance is InputProvenance.UNKNOWN


def test_injection_registry_is_bounded() -> None:
    classifier = HumanActivityClassifier(
        max_injection_windows=2,
        injection_tolerance_ms=0,
    )
    classifier.record_agent_input("a", 1, 2)
    classifier.record_agent_input("b", 3, 4)
    classifier.record_agent_input("c", 5, 6)

    old = classifier.assess(
        LastInputSample(observed_tick_ms=10, last_input_tick_ms=2)
    )
    latest = classifier.assess(
        LastInputSample(observed_tick_ms=10, last_input_tick_ms=6)
    )

    assert old.provenance is InputProvenance.HUMAN
    assert latest.provenance is InputProvenance.AGENT


def test_unreasonably_long_injection_window_is_rejected() -> None:
    classifier = HumanActivityClassifier()

    with pytest.raises(ValueError, match="60 seconds"):
        classifier.record_agent_input("action", 1, 60_002)
