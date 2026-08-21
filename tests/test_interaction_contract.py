"""Pure regression tests for the fail-closed interaction contract."""

from __future__ import annotations

import pytest

from open_compute.interaction import (
    InteractionContractError,
    ObservationRegistry,
    describe_window,
    resolve_window_reference,
    select_target,
    send_text_verified,
)


WINDOW = {"hwnd": 42, "pid": 7001, "title": "YouTube Studio - Edge"}


def test_window_descriptor_has_stable_aliases_and_token() -> None:
    first = describe_window(WINDOW)
    second = describe_window(dict(WINDOW))

    assert first["window_id"] == first["hwnd"] == 42
    assert first["process_id"] == first["pid"] == 7001
    assert first["window_token"].startswith("win_1_")
    assert first["window_token"] == second["window_token"]


def test_window_token_resolves_only_to_the_same_current_identity() -> None:
    issued = describe_window(WINDOW)

    assert resolve_window_reference(
        issued["window_token"], [WINDOW], {issued["window_token"]: issued}
    )["hwnd"] == 42

    changed = [{**WINDOW, "title": "Codex"}]
    with pytest.raises(InteractionContractError) as caught:
        resolve_window_reference(
            issued["window_token"], changed, {issued["window_token"]: issued}
        )
    assert caught.value.code == "window_identity_changed"


def test_fabricated_window_descriptor_is_rejected_until_issued() -> None:
    with pytest.raises(InteractionContractError) as caught:
        resolve_window_reference(WINDOW, [WINDOW], {})
    assert caught.value.code == "window_token_unknown"


def test_observation_is_single_use_and_rejects_changed_state() -> None:
    registry = ObservationRegistry()
    observation = registry.record(
        kind="screenshot",
        payload=b"frame-a",
        window=WINDOW,
        frame={"left": 0, "top": 0, "width": 100, "height": 100},
    )

    claimed = registry.claim(observation["observation_id"], payload=b"frame-a")
    assert claimed["kind"] == "screenshot"

    with pytest.raises(InteractionContractError) as caught:
        registry.claim(observation["observation_id"], payload=b"frame-a")
    assert caught.value.code == "stale_observation"

    changed = registry.record(kind="uia_tree", payload=[{"name": "Save"}])
    with pytest.raises(InteractionContractError) as caught:
        registry.claim(changed["observation_id"], payload=[{"name": "Cancel"}])
    assert caught.value.code == "observation_changed"

    wrong_window = registry.record(
        kind="screenshot",
        payload=b"frame-c",
        window=WINDOW,
    )
    with pytest.raises(InteractionContractError) as caught:
        registry.claim(
            wrong_window["observation_id"],
            payload=b"frame-c",
            window={**WINDOW, "hwnd": 99},
        )
    assert caught.value.code == "observation_window_changed"


def test_target_selection_is_exact_first_and_reports_alternatives() -> None:
    match = select_target(
        "Erstellen",
        [
            {"name": "Wiederherstellen", "role": "Button", "visible": True},
            {"name": "Erstellen", "role": "Button", "visible": True},
        ],
    )

    assert match.target["name"] == "Erstellen"
    assert match.match_type == "exact"
    assert match.score == 1.0
    assert match.alternatives[0]["name"] == "Wiederherstellen"


def test_target_selection_rejects_ambiguity_and_weak_contains_match() -> None:
    with pytest.raises(InteractionContractError) as ambiguous:
        select_target(
            "Save",
            [
                {"name": "Save", "role": "Button", "visible": True},
                {"name": "Save", "role": "MenuItem", "visible": True},
            ],
        )
    assert ambiguous.value.code == "ambiguous_target"
    assert len(ambiguous.value.details["candidates"]) == 2

    with pytest.raises(InteractionContractError) as weak:
        select_target(
            "Erstellen",
            [{"name": "Wiederherstellen", "role": "Button", "visible": True}],
        )
    assert weak.value.code == "low_confidence_target"


@pytest.mark.parametrize("length", [100, 500, 2_000, 2_501])
def test_long_text_is_segmented_and_fully_accounted(length: int) -> None:
    chunks: list[int] = []

    result = send_text_verified(
        "x" * length,
        send_chunk=lambda chunk: chunks.append(len(chunk)) or len(chunk),
        check_focus=lambda: describe_window(WINDOW),
        chunk_chars=100,
    )

    assert result["requested_chars"] == length
    assert result["sent_chars"] == length
    assert result["complete"] is True
    assert result["status"] == "complete"
    assert max(chunks) <= 100
    assert "text" not in result


def test_focus_loss_between_segments_stops_without_silent_partial_write() -> None:
    focus_checks = iter(
        [describe_window(WINDOW), describe_window({**WINDOW, "hwnd": 99})]
    )
    sent: list[str] = []

    result = send_text_verified(
        "x" * 250,
        send_chunk=lambda chunk: sent.append(chunk) or len(chunk),
        check_focus=lambda: next(focus_checks),
        expected_window=WINDOW,
        chunk_chars=100,
    )

    assert result["requested_chars"] == 250
    assert result["sent_chars"] == 100
    assert result["complete"] is False
    assert result["status"] == "partial"
    assert result["code"] == "foreground_window_mismatch"
    assert result["expected_window"]["window_id"] == 42
    assert result["actual_window"]["window_id"] == 99
    assert len(sent) == 1


def test_structured_focus_error_preserves_expected_and_actual_ids() -> None:
    actual = describe_window({**WINDOW, "hwnd": 99, "pid": 7002})

    def _changed_focus():
        raise InteractionContractError(
            "foreground_window_mismatch",
            "foreground changed",
            expected_window=describe_window(WINDOW),
            actual_window=actual,
        )

    result = send_text_verified(
        "secret",
        send_chunk=lambda chunk: len(chunk),
        check_focus=_changed_focus,
        expected_window=WINDOW,
    )

    assert result["code"] == "foreground_window_mismatch"
    assert result["expected_window"]["window_id"] == 42
    assert result["actual_window"]["window_id"] == 99
    assert result["sent_chars"] == 0


def test_short_send_is_reported_as_partial() -> None:
    result = send_text_verified(
        "x" * 100,
        send_chunk=lambda _chunk: 60,
        check_focus=lambda: describe_window(WINDOW),
    )

    assert result["requested_chars"] == 100
    assert result["sent_chars"] == 60
    assert result["complete"] is False
    assert result["status"] == "partial"
    assert result["code"] == "short_write"
