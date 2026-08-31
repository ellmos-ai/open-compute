"""Profile-driven perception and capability filtering for computer use.

The filter runs locally, before observations reach a reasoning model.  A host
supplies a strict JSON profile describing the semantic budget, visual lens,
excluded UI and allowed actions for its use case.  The module is provider-
agnostic and uses only the Python standard library.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from typing import Any, Iterable


_PROFILE_FIELDS = {
    "profileId",
    "semanticFirst",
    "maxElements",
    "maxCharacters",
    "textLimit",
    "valuePolicy",
    "selectionLimit",
    "focusRadius",
    "visualLens",
    "allowFullscreen",
    "excludeElementNameContains",
    "excludeWindowTitleContains",
    "allowedTools",
    "allowedActionTypes",
}
_VALUE_POLICIES = {"none", "focused-only", "all-bounded"}
_FILTER_TOOLS = {
    "observe_filtered",
    "capture_filtered",
    "signal_show",
    "signal_hide",
    "signal_status",
    "do",
}
_ACTION_TYPES = {
    "left_click",
    "right_click",
    "middle_click",
    "double_click",
    "triple_click",
    "mouse_move",
    "scroll",
    "left_click_drag",
    "mouse_down",
    "mouse_up",
    "key_down",
    "key_up",
    "type",
    "key",
    "wait",
    "screenshot",
    "cursor_position",
    "launch_app",
    "activate_window",
}
_FOCUS_KINDS = {"follow-me", "click-focus", "fixed-focus"}


def _bounded_string(value: Any, label: str, *, maximum: int = 120) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{label} must be a non-empty string of at most {maximum} characters")
    return value.strip()


def _string_tuple(value: Any, label: str, *, allowed: set[str] | None = None) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > 24:
        raise ValueError(f"{label} must be a non-empty list with at most 24 entries")
    out: list[str] = []
    for item in value:
        clean = _bounded_string(item, label, maximum=80)
        if allowed is not None and clean not in allowed:
            raise ValueError(f"{label} contains an unsupported value: {clean}")
        if clean in out:
            raise ValueError(f"{label} must not contain duplicates")
        out.append(clean)
    return tuple(out)


def _optional_string_tuple(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 24:
        raise ValueError(f"{label} must be a list with at most 24 entries")
    out: list[str] = []
    for item in value:
        clean = _bounded_string(item, label, maximum=80)
        if clean.casefold() in {entry.casefold() for entry in out}:
            raise ValueError(f"{label} must not contain duplicates")
        out.append(clean)
    return tuple(out)


@dataclass(frozen=True)
class FilterProfile:
    """Strict host-supplied filter profile used before model delivery."""

    profile_id: str
    semantic_first: bool
    max_elements: int
    max_characters: int
    text_limit: int
    value_policy: str
    selection_limit: int
    focus_radius: float
    visual_lens_width: int
    visual_lens_height: int
    allow_fullscreen: bool
    exclude_element_name_contains: tuple[str, ...]
    exclude_window_title_contains: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    allowed_action_types: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "FilterProfile":
        if not isinstance(raw, dict):
            raise ValueError("filter profile must be a JSON object")
        unknown = sorted(set(raw) - _PROFILE_FIELDS)
        if unknown:
            raise ValueError(f"unknown filter profile fields: {', '.join(unknown)}")

        profile_id = _bounded_string(raw.get("profileId"), "profileId")
        semantic_first = raw.get("semanticFirst", True)
        max_elements = raw.get("maxElements", 12)
        max_characters = raw.get("maxCharacters", 1200)
        text_limit = raw.get("textLimit", 120)
        value_policy = raw.get("valuePolicy", "focused-only")
        selection_limit = raw.get("selectionLimit", 160)
        focus_radius = raw.get("focusRadius", 0.18)
        visual_lens = raw.get("visualLens", {"width": 400, "height": 400})
        allow_fullscreen = raw.get("allowFullscreen", False)

        if semantic_first is not True:
            raise ValueError("semanticFirst must be true in filter profile v1")
        if not isinstance(max_elements, int) or not 1 <= max_elements <= 50:
            raise ValueError("maxElements must be in 1..50")
        if not isinstance(max_characters, int) or not 256 <= max_characters <= 20_000:
            raise ValueError("maxCharacters must be in 256..20000")
        if not isinstance(text_limit, int) or not 8 <= text_limit <= 350:
            raise ValueError("textLimit must be in 8..350")
        if value_policy not in _VALUE_POLICIES:
            raise ValueError(f"valuePolicy must be one of {sorted(_VALUE_POLICIES)}")
        if not isinstance(selection_limit, int) or not 0 <= selection_limit <= 350:
            raise ValueError("selectionLimit must be in 0..350")
        if (
            isinstance(focus_radius, bool)
            or not isinstance(focus_radius, (int, float))
            or not 0.01 <= float(focus_radius) <= 1.0
        ):
            raise ValueError("focusRadius must be in 0.01..1.0")
        if not isinstance(visual_lens, dict) or set(visual_lens) != {"width", "height"}:
            raise ValueError("visualLens must contain only width and height")
        lens_width = visual_lens.get("width")
        lens_height = visual_lens.get("height")
        if (
            not isinstance(lens_width, int)
            or not isinstance(lens_height, int)
            or not 64 <= lens_width <= 2048
            or not 64 <= lens_height <= 2048
        ):
            raise ValueError("visualLens width and height must be in 64..2048")
        if not isinstance(allow_fullscreen, bool):
            raise ValueError("allowFullscreen must be a boolean")

        allowed_action_types = _string_tuple(
            raw.get("allowedActionTypes"),
            "allowedActionTypes",
            allowed=_ACTION_TYPES,
        )
        if not allow_fullscreen and "screenshot" in allowed_action_types:
            raise ValueError("allowFullscreen must be true before screenshot actions are allowed")

        return cls(
            profile_id=profile_id,
            semantic_first=semantic_first,
            max_elements=max_elements,
            max_characters=max_characters,
            text_limit=text_limit,
            value_policy=value_policy,
            selection_limit=selection_limit,
            focus_radius=float(focus_radius),
            visual_lens_width=lens_width,
            visual_lens_height=lens_height,
            allow_fullscreen=allow_fullscreen,
            exclude_element_name_contains=_optional_string_tuple(
                raw.get("excludeElementNameContains"),
                "excludeElementNameContains",
            ),
            exclude_window_title_contains=_optional_string_tuple(
                raw.get("excludeWindowTitleContains"),
                "excludeWindowTitleContains",
            ),
            allowed_tools=_string_tuple(
                raw.get("allowedTools"),
                "allowedTools",
                allowed=_FILTER_TOOLS,
            ),
            allowed_action_types=allowed_action_types,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profileId": self.profile_id,
            "semanticFirst": self.semantic_first,
            "maxElements": self.max_elements,
            "maxCharacters": self.max_characters,
            "textLimit": self.text_limit,
            "valuePolicy": self.value_policy,
            "selectionLimit": self.selection_limit,
            "focusRadius": self.focus_radius,
            "visualLens": {
                "width": self.visual_lens_width,
                "height": self.visual_lens_height,
            },
            "allowFullscreen": self.allow_fullscreen,
            "excludeElementNameContains": list(self.exclude_element_name_contains),
            "excludeWindowTitleContains": list(self.exclude_window_title_contains),
            "allowedTools": list(self.allowed_tools),
            "allowedActionTypes": list(self.allowed_action_types),
        }


def _focus(raw: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("focus must be a JSON object")
    kind = raw.get("kind")
    x = raw.get("x")
    y = raw.get("y")
    if kind not in _FOCUS_KINDS:
        raise ValueError(f"focus kind must be one of {sorted(_FOCUS_KINDS)}")
    if (
        isinstance(x, bool)
        or isinstance(y, bool)
        or not isinstance(x, (int, float))
        or not isinstance(y, (int, float))
        or not 0 <= float(x) <= 1
        or not 0 <= float(y) <= 1
    ):
        raise ValueError("focus x and y must be normalized numbers")
    selected_text = raw.get("selectedText", "")
    target_name = raw.get("targetName", "")
    if not isinstance(selected_text, str) or len(selected_text) > 100_000:
        raise ValueError("selectedText must be a bounded string")
    if not isinstance(target_name, str) or len(target_name) > 350:
        raise ValueError("targetName must be a bounded string")
    return {
        "kind": kind,
        "x": float(x),
        "y": float(y),
        "selectedText": selected_text,
        "targetName": target_name,
    }


def _truncate(text: Any, limit: int) -> str:
    clean = text if isinstance(text, str) else ""
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)] + "…"


def _selection(text: str, limit: int) -> dict[str, Any] | None:
    if not text:
        return None
    if len(text) <= limit:
        return {"kind": "text", "text": text}
    return {"kind": "digest", "length": len(text), "sha256": sha256(text.encode("utf-8")).hexdigest()}


def _is_excluded(name: str, profile: FilterProfile) -> bool:
    folded = name.casefold()
    return any(fragment.casefold() in folded for fragment in profile.exclude_element_name_contains)


def _center(element: dict[str, Any]) -> tuple[float, float] | None:
    center = element.get("center_norm")
    if (
        not isinstance(center, (list, tuple))
        or len(center) != 2
        or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in center)
    ):
        return None
    x, y = float(center[0]), float(center[1])
    if not 0 <= x <= 1 or not 0 <= y <= 1:
        return None
    return x, y


def _payload_length(packet: dict[str, Any]) -> int:
    return len(json.dumps(packet, ensure_ascii=False, separators=(",", ":")))


def filter_uia_elements(
    elements: Iterable[dict[str, Any]],
    *,
    focus: dict[str, Any],
    profile: FilterProfile,
) -> dict[str, Any]:
    """Return a compact semantic packet around the declared focus."""

    focus_value = _focus(focus)
    source = list(elements)
    candidates: list[tuple[float, dict[str, Any]]] = []
    excluded = 0
    target_folded = focus_value["targetName"].casefold()
    for element in source:
        if not isinstance(element, dict) or element.get("visible", True) is not True:
            excluded += 1
            continue
        name = element.get("name", "")
        if not isinstance(name, str) or _is_excluded(name, profile):
            excluded += 1
            continue
        center = _center(element)
        if center is None:
            excluded += 1
            continue
        distance = math.hypot(center[0] - focus_value["x"], center[1] - focus_value["y"])
        if target_folded and name.casefold() == target_folded:
            distance = -1.0
        if distance <= profile.focus_radius or distance < 0:
            candidates.append((distance, element))
    candidates.sort(key=lambda item: (item[0], str(item[1].get("name", "")).casefold()))

    compact: list[dict[str, Any]] = []
    for index, (distance, element) in enumerate(candidates[: profile.max_elements]):
        item: dict[str, Any] = {
            "name": _truncate(element.get("name", ""), profile.text_limit),
            "role": _truncate(element.get("role", ""), min(profile.text_limit, 80)),
            "center": [round(value, 5) for value in (_center(element) or (0.0, 0.0))],
        }
        rect = element.get("rect_px")
        if (
            isinstance(rect, (list, tuple))
            and len(rect) == 4
            and all(isinstance(value, int) for value in rect)
        ):
            item["rect"] = list(rect)
        include_value = profile.value_policy == "all-bounded" or (
            profile.value_policy == "focused-only" and index == 0
        )
        if include_value and isinstance(element.get("value"), str) and element["value"]:
            value = element["value"]
            if len(value) <= profile.text_limit:
                item["value"] = value
            else:
                item["value"] = {
                    "kind": "digest",
                    "length": len(value),
                    "sha256": sha256(value.encode("utf-8")).hexdigest(),
                }
        compact.append(item)

    selection = None if focus_value["kind"] == "click-focus" else _selection(
        focus_value["selectedText"], profile.selection_limit
    )
    packet: dict[str, Any] = {
        "type": "filtered-perception",
        "profileId": profile.profile_id,
        "focus": {
            "kind": focus_value["kind"],
            "x": focus_value["x"],
            "y": focus_value["y"],
            "selection": selection,
        },
        "elements": compact,
        "metrics": {
            "sourceElements": len(source),
            "includedElements": len(compact),
            "excludedElements": excluded,
            "omittedElements": len(source) - excluded - len(compact),
            "payloadCharacters": 0,
        },
    }

    while True:
        previous = packet["metrics"]["payloadCharacters"]
        length = _payload_length(packet)
        packet["metrics"]["payloadCharacters"] = length
        stabilized = previous == length
        final_length = _payload_length(packet)
        if final_length <= profile.max_characters and stabilized:
            break
        if final_length <= profile.max_characters:
            continue
        if not packet["elements"]:
            raise ValueError("filter profile maxCharacters is too small for its focus packet")
        packet["elements"].pop()
        packet["metrics"]["includedElements"] = len(packet["elements"])
        packet["metrics"]["omittedElements"] = (
            len(source) - excluded - len(packet["elements"])
        )
        packet["metrics"]["payloadCharacters"] = 0

    return packet


def resolve_visual_region(
    *,
    profile: FilterProfile,
    focus: dict[str, Any],
    virtual_desktop: dict[str, Any],
) -> dict[str, Any]:
    """Resolve the profile lens to one clamped absolute desktop rectangle."""

    focus_value = _focus(focus)
    if not isinstance(virtual_desktop, dict):
        raise ValueError("virtualDesktop geometry is required")
    values = [virtual_desktop.get(key) for key in ("left", "top", "width", "height")]
    if not all(isinstance(value, int) for value in values):
        raise ValueError("virtualDesktop geometry must contain integer values")
    left, top, width, height = values
    if width <= 0 or height <= 0:
        raise ValueError("virtualDesktop dimensions must be positive")
    lens_width = min(profile.visual_lens_width, width)
    lens_height = min(profile.visual_lens_height, height)
    center_x = left + focus_value["x"] * width
    center_y = top + focus_value["y"] * height
    lens_left = round(center_x - lens_width / 2)
    lens_top = round(center_y - lens_height / 2)
    lens_left = max(left, min(left + width - lens_width, lens_left))
    lens_top = max(top, min(top + height - lens_height, lens_top))
    return {
        "left": lens_left,
        "top": lens_top,
        "width": lens_width,
        "height": lens_height,
        "virtualDesktop": {
            "left": left,
            "top": top,
            "width": width,
            "height": height,
        },
    }


def validate_profiled_actions(actions: Iterable[dict[str, Any]], profile: FilterProfile) -> None:
    """Fail before execution when an action is outside the profile allowlist."""

    for action in actions:
        if not isinstance(action, dict):
            raise ValueError("profiled action must be a JSON object")
        action_type = action.get("type", action.get("action"))
        if action_type not in profile.allowed_action_types:
            raise PermissionError(f"action {action_type!r} is outside filter profile {profile.profile_id!r}")


def excluded_window_rectangles(
    windows: Iterable[dict[str, Any]], profile: FilterProfile
) -> list[dict[str, int]]:
    """Return absolute rectangles for windows the profile excludes visually."""

    fragments = tuple(value.casefold() for value in profile.exclude_window_title_contains)
    if not fragments:
        return []
    out: list[dict[str, int]] = []
    for window in windows:
        title = window.get("title", "") if isinstance(window, dict) else ""
        rect = window.get("rect") if isinstance(window, dict) else None
        if not isinstance(title, str) or not any(fragment in title.casefold() for fragment in fragments):
            continue
        if not isinstance(rect, dict):
            continue
        values = {key: rect.get(key) for key in ("left", "top", "width", "height")}
        if all(isinstance(value, int) for value in values.values()):
            out.append(values)
    return out
