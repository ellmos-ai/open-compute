import json

import pytest

from open_compute.perception_filter import (
    FilterProfile,
    filter_uia_elements,
    resolve_visual_region,
    validate_profiled_actions,
)


def cowork_profile(**overrides):
    profile = {
        "profileId": "cowork-pointer-budget-v1",
        "semanticFirst": True,
        "maxElements": 3,
        "maxCharacters": 900,
        "textLimit": 80,
        "valuePolicy": "focused-only",
        "selectionLimit": 16,
        "focusRadius": 0.22,
        "visualLens": {"width": 400, "height": 400},
        "allowFullscreen": False,
        "excludeElementNameContains": ["Cowork Companion", "ChatGPT", "Claude"],
        "excludeWindowTitleContains": ["Cowork Protocol Companion", "ChatGPT", "Claude"],
        "allowedTools": [
            "observe_filtered",
            "capture_filtered",
            "signal_show",
            "signal_hide",
            "signal_status",
            "do",
        ],
        "allowedActionTypes": ["mouse_move", "left_click", "type", "key", "scroll", "wait"],
    }
    profile.update(overrides)
    return FilterProfile.from_dict(profile)


def element(name, x, y, *, value="", role="Button", visible=True):
    return {
        "name": name,
        "role": role,
        "value": value,
        "rect_px": [int(x * 1000), int(y * 800), 80, 30],
        "center_norm": [x, y],
        "visible": visible,
        "depth": 4,
    }


def test_profile_is_strict_bounded_and_serializable():
    profile = cowork_profile()
    assert profile.profile_id == "cowork-pointer-budget-v1"
    assert profile.allowed_tools == (
        "observe_filtered",
        "capture_filtered",
        "signal_show",
        "signal_hide",
        "signal_status",
        "do",
    )
    assert profile.to_dict()["visualLens"] == {"width": 400, "height": 400}

    with pytest.raises(ValueError, match="unknown filter profile fields"):
        FilterProfile.from_dict({**profile.to_dict(), "sendWholeDesktop": True})
    with pytest.raises(ValueError, match="maxCharacters"):
        cowork_profile(maxCharacters=100)
    with pytest.raises(ValueError, match="semanticFirst"):
        cowork_profile(semanticFirst=False)
    with pytest.raises(ValueError, match="allowedTools"):
        cowork_profile(allowedTools=["capture"])
    with pytest.raises(ValueError, match="allowFullscreen"):
        cowork_profile(allowedActionTypes=["screenshot"])


def test_follow_me_keeps_only_nearby_semantics_and_bounded_selection():
    profile = cowork_profile()
    packet = filter_uia_elements(
        [
            element("Full name", 0.51, 0.48, value="Lukas"),
            element("Email", 0.55, 0.56, value="secret@example.test", role="Edit"),
            element("Cowork Companion chat", 0.49, 0.49, value="own transcript", role="Edit"),
            element("Delete account", 0.95, 0.95),
            element("Hidden", 0.50, 0.50, visible=False),
        ],
        focus={
            "kind": "follow-me",
            "x": 0.5,
            "y": 0.5,
            "selectedText": "a deliberately long selected passage",
        },
        profile=profile,
    )

    assert packet["profileId"] == "cowork-pointer-budget-v1"
    assert packet["focus"]["kind"] == "follow-me"
    assert packet["focus"]["selection"]["kind"] == "digest"
    assert packet["focus"]["selection"]["length"] == 36
    assert len(packet["focus"]["selection"]["sha256"]) == 64
    assert [item["name"] for item in packet["elements"]] == ["Full name", "Email"]
    assert packet["elements"][0]["value"] == "Lukas"
    assert "value" not in packet["elements"][1]
    assert "Delete account" not in json.dumps(packet)
    assert packet["metrics"]["sourceElements"] == 5
    assert packet["metrics"]["includedElements"] == 2
    assert packet["metrics"]["excludedElements"] == 2
    assert packet["metrics"]["payloadCharacters"] <= profile.max_characters


def test_click_focus_excludes_text_selection_and_uses_the_clicked_target():
    packet = filter_uia_elements(
        [
            element("First", 0.2, 0.2, value="one"),
            element("Clicked field", 0.8, 0.7, value="two", role="Edit"),
        ],
        focus={
            "kind": "click-focus",
            "x": 0.8,
            "y": 0.7,
            "targetName": "Clicked field",
            "selectedText": "must not leak into click-only mode",
        },
        profile=cowork_profile(),
    )

    assert packet["focus"]["selection"] is None
    assert packet["elements"][0]["name"] == "Clicked field"
    assert packet["elements"][0]["value"] == "two"


def test_payload_budget_removes_elements_instead_of_overflowing():
    profile = cowork_profile(maxCharacters=520, maxElements=8, textLimit=70)
    elements = [element(f"Control {index} " + "x" * 80, 0.50 + index / 1000, 0.5) for index in range(8)]
    packet = filter_uia_elements(
        elements,
        focus={"kind": "follow-me", "x": 0.5, "y": 0.5, "selectedText": ""},
        profile=profile,
    )

    assert packet["metrics"]["includedElements"] < len(elements)
    assert packet["metrics"]["payloadCharacters"] <= 520
    assert len(json.dumps(packet, ensure_ascii=False, separators=(",", ":"))) <= 520


def test_visual_lens_is_pointer_bounded_and_clamped_to_the_virtual_desktop():
    profile = cowork_profile()
    region = resolve_visual_region(
        profile=profile,
        focus={"kind": "follow-me", "x": 0.99, "y": 0.01, "selectedText": ""},
        virtual_desktop={"left": -1920, "top": 0, "width": 3840, "height": 1080},
    )

    assert region == {
        "left": 1520,
        "top": 0,
        "width": 400,
        "height": 400,
        "virtualDesktop": {"left": -1920, "top": 0, "width": 3840, "height": 1080},
    }
    assert region["width"] * region["height"] < 3840 * 1080


def test_profile_rejects_unneeded_computer_actions_before_the_executor():
    profile = cowork_profile()
    validate_profiled_actions(
        [{"type": "mouse_move", "x": 0.5, "y": 0.5}, {"type": "left_click", "x": 0.5, "y": 0.5}],
        profile,
    )
    with pytest.raises(PermissionError, match="outside filter profile"):
        validate_profiled_actions([{"type": "launch_app", "app_name": "powershell"}], profile)


# --- bundled perception modes (Ticket T-20260919-184978745) ---------------

def test_bundled_modes_load_validate_and_stay_within_their_budgets():
    from open_compute.perception_filter import PERCEPTION_MODES, load_mode_profile

    assert PERCEPTION_MODES == ("observe-lite", "observe-full", "act")
    profiles = {name: FilterProfile.from_dict(load_mode_profile(name))
                for name in PERCEPTION_MODES}
    lite, full, act = (profiles[name] for name in PERCEPTION_MODES)

    # observe-lite is the frugal one — that is its whole reason to exist.
    assert lite.max_characters < full.max_characters
    assert lite.max_elements < full.max_elements
    assert lite.focus_radius <= full.focus_radius

    # No mode hands out the full screen, and the lens stays the small one.
    assert not any(profile.allow_fullscreen for profile in profiles.values())
    assert all(
        (profile.visual_lens_width, profile.visual_lens_height) == (400, 400)
        for profile in profiles.values()
    )

    # Watching is not acting: only `act` may drive the desktop.
    assert "do" not in lite.allowed_tools
    assert "do" not in full.allowed_tools
    assert "do" in act.allowed_tools

    # Assistant windows are redacted in every mode.
    for profile in profiles.values():
        assert "Claude" in profile.exclude_window_title_contains


def test_unknown_mode_is_rejected():
    from open_compute.perception_filter import load_mode_profile

    with pytest.raises(ValueError, match="mode"):
        load_mode_profile("observe-turbo")


def test_refine_three_stage_escalation_ladder_and_budget_gate():
    from open_compute.perception_filter import ESCALATION_LADDER, refine

    assert ESCALATION_LADDER == ("observe-lite", "observe-full", "act")

    # Stage 1 -> Stage 2 auto-justified when observe-lite omitted elements
    step1 = refine(
        "observe-lite",
        packet={"metrics": {"includedElements": 3, "omittedElements": 5}},
        remaining_budget=2,
    )
    assert step1["escalated"] is True
    assert step1["previousMode"] == "observe-lite"
    assert step1["mode"] == "observe-full"
    assert step1["stage"] == 2
    assert step1["budgetRemaining"] == 1
    assert step1["reason"] == "omitted_elements"

    # Stage 1 -> Stage 2 rejected without reason when packet is already sufficient
    with pytest.raises(ValueError, match="requires a reason"):
        refine(
            "observe-lite",
            packet={"metrics": {"includedElements": 2, "omittedElements": 0}},
            remaining_budget=2,
        )

    # Stage 2 -> Stage 3 requires explicit reason
    with pytest.raises(ValueError, match="explicit non-empty `reason`"):
        refine("observe-full", remaining_budget=1)

    step2 = refine(
        "observe-full",
        reason="Target control is custom-drawn canvas; need bounded visual lens and click.",
        remaining_budget=step1["budgetRemaining"],
    )
    assert step2["escalated"] is True
    assert step2["previousMode"] == "observe-full"
    assert step2["mode"] == "act"
    assert step2["stage"] == 3
    assert step2["budgetRemaining"] == 0

    # Budget gate blocks escalation when remaining_budget == 0
    blocked = refine("observe-lite", reason="want more", remaining_budget=0)
    assert blocked["escalated"] is False
    assert blocked["mode"] == "observe-lite"
    assert blocked["reason"] == "budget_exhausted"

    # Top stage stays at act without consuming budget
    top = refine("act", reason="already at top", remaining_budget=3)
    assert top["escalated"] is False
    assert top["mode"] == "act"
    assert top["stage"] == 3

