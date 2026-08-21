"""Fail-closed desktop interaction state and postcondition helpers.

The module is deliberately stdlib-only.  It centralizes four contracts used by
the MCP adapter and the Windows executor:

* robust window descriptors/tokens,
* one-shot observation identities,
* deterministic exact-first UI target selection, and
* segmented text delivery with explicit character accounting.

No helper stores or returns the text being typed.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping


class InteractionContractError(RuntimeError):
    """Structured fail-closed interaction error."""

    def __init__(self, code: str, reason: str, **details: Any) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason
        self.details = details

    def to_result(self) -> dict[str, Any]:
        return {
            "result": "interaction_rejected",
            "status": self.code.upper(),
            "code": self.code,
            "reason": self.reason,
            **self.details,
        }


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().casefold()


def _window_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        window_id = int(value.get("window_id", value.get("hwnd")))
        process_id = int(value.get("process_id", value.get("pid")))
        title = str(value["title"]).strip()
    except (KeyError, TypeError, ValueError) as exc:
        raise InteractionContractError(
            "window_identity_invalid",
            "window identity requires positive window_id/hwnd, process_id/pid, and title",
        ) from exc
    if window_id <= 0 or process_id <= 0 or not title:
        raise InteractionContractError(
            "window_identity_invalid",
            "window identity requires positive window_id/hwnd, process_id/pid, and title",
        )
    return {"hwnd": window_id, "pid": process_id, "title": title}


def _same_window(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    a = _window_identity(left)
    b = _window_identity(right)
    return (
        a["hwnd"] == b["hwnd"]
        and a["pid"] == b["pid"]
        and _normalize_title(a["title"]) == _normalize_title(b["title"])
    )


def describe_window(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a compatibility-preserving descriptor with a stable token."""

    identity = _window_identity(value)
    material = (
        f"{identity['hwnd']}\0{identity['pid']}\0"
        f"{_normalize_title(identity['title'])}"
    ).encode("utf-8")
    token = "win_1_" + hashlib.sha256(material).hexdigest()[:24]
    result = dict(value)
    result.update(
        {
            "hwnd": identity["hwnd"],
            "pid": identity["pid"],
            "window_id": identity["hwnd"],
            "process_id": identity["pid"],
            "title": identity["title"],
            "window_token": token,
        }
    )
    return result


def resolve_window_reference(
    reference: str | Mapping[str, Any],
    current_windows: Iterable[Mapping[str, Any]],
    issued_tokens: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve a full descriptor or issued token against the live window list."""

    token_map = issued_tokens or {}
    provided_token: str | None = None
    if isinstance(reference, str):
        provided_token = reference
        expected_raw = token_map.get(reference)
        if expected_raw is None:
            raise InteractionContractError(
                "window_token_unknown",
                "window token was not issued by this server process; call list_windows again",
                window_token=reference,
            )
    elif isinstance(reference, Mapping):
        token = str(reference.get("window_token", "")).strip()
        provided_token = token or None
        has_identity = any(
            key in reference for key in ("hwnd", "window_id")
        ) and any(key in reference for key in ("pid", "process_id"))
        if not has_identity and token:
            expected_raw = token_map.get(token)
            if expected_raw is None:
                raise InteractionContractError(
                    "window_token_unknown",
                    "window token was not issued by this server process; call list_windows again",
                    window_token=token,
                )
        else:
            expected_raw = reference
    else:
        raise InteractionContractError(
            "window_reference_invalid",
            "expected_window must be a window descriptor or window_token string",
        )

    expected = describe_window(expected_raw)
    if provided_token is not None and provided_token != expected["window_token"]:
        raise InteractionContractError(
            "window_token_mismatch",
            "provided window_token does not match the descriptor identity",
            provided_window_token=provided_token,
            expected_window_token=expected["window_token"],
        )
    issued = token_map.get(expected["window_token"])
    if issued is None:
        raise InteractionContractError(
            "window_token_unknown",
            "window descriptor/token was not issued by this server process; call list_windows again",
            window_token=expected["window_token"],
        )
    if not _same_window(expected, issued):
        raise InteractionContractError(
            "window_token_mismatch",
            "window descriptor does not match the server-issued token identity",
            expected_window=expected,
            issued_window=describe_window(issued),
        )
    live = [describe_window(window) for window in current_windows]
    exact = [window for window in live if _same_window(expected, window)]
    if len(exact) == 1:
        return exact[0]
    same_handle = [window for window in live if window["hwnd"] == expected["hwnd"]]
    if same_handle:
        raise InteractionContractError(
            "window_identity_changed",
            "the bound window handle now has a different process or title",
            expected_window=expected,
            actual_windows=same_handle,
        )
    raise InteractionContractError(
        "window_not_found",
        "the bound window no longer exists",
        expected_window=expected,
        candidates=live,
    )


def _canonical_payload(payload: Any) -> bytes:
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, bytearray):
        return bytes(payload)
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


class ObservationRegistry:
    """Small in-memory registry of one-shot capture/tree observations."""

    def __init__(self, max_records: int = 128) -> None:
        self.max_records = max(1, int(max_records))
        self._records: dict[str, dict[str, Any]] = {}

    def clear(self) -> None:
        self._records.clear()

    def record(
        self,
        *,
        kind: str,
        payload: Any,
        window: Mapping[str, Any] | None = None,
        frame: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        observation_id = "obs_1_" + uuid.uuid4().hex
        signature = hashlib.sha256(_canonical_payload(payload)).hexdigest()
        record: dict[str, Any] = {
            "observation_id": observation_id,
            "kind": str(kind),
            "signature": signature,
            "created_at": time.time(),
            "consumed": False,
            "window": describe_window(window) if window is not None else None,
            "coordinate_frame": dict(frame) if frame is not None else None,
        }
        self._records[observation_id] = record
        while len(self._records) > self.max_records:
            self._records.pop(next(iter(self._records)))
        return self.public(record)

    def public(self, record: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "observation_id": record["observation_id"],
            "kind": record["kind"],
            "sha256": record["signature"],
            "created_at": record["created_at"],
            "window": record.get("window"),
            "coordinate_frame": record.get("coordinate_frame"),
        }

    def peek(self, observation_id: str) -> dict[str, Any]:
        """Return public metadata without consuming the observation."""

        record = self._records.get(str(observation_id))
        if record is None:
            raise InteractionContractError(
                "observation_unknown",
                "observation_id is unknown; observe again before acting",
                observation_id=observation_id,
            )
        if record["consumed"]:
            raise InteractionContractError(
                "stale_observation",
                "observation_id has already been consumed or invalidated",
                observation_id=observation_id,
            )
        return self.public(record)

    def claim(
        self,
        observation_id: str,
        *,
        payload: Any,
        window: Mapping[str, Any] | None = None,
        frame: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self._records.get(str(observation_id))
        if record is None:
            raise InteractionContractError(
                "observation_unknown",
                "observation_id is unknown; observe again before acting",
                observation_id=observation_id,
            )
        if record["consumed"]:
            raise InteractionContractError(
                "stale_observation",
                "observation_id has already been consumed or invalidated",
                observation_id=observation_id,
            )
        current_signature = hashlib.sha256(_canonical_payload(payload)).hexdigest()
        if current_signature != record["signature"]:
            record["consumed"] = True
            raise InteractionContractError(
                "observation_changed",
                "screen/tree state changed after observation; observe again",
                observation_id=observation_id,
                expected_sha256=record["signature"],
                actual_sha256=current_signature,
            )
        if record.get("window") is not None and window is not None:
            if not _same_window(record["window"], window):
                record["consumed"] = True
                raise InteractionContractError(
                    "observation_window_changed",
                    "the observation belongs to a different window identity",
                    observation_id=observation_id,
                    expected_window=record["window"],
                    actual_window=describe_window(window),
                )
        if record.get("coordinate_frame") is not None and frame is not None:
            if dict(record["coordinate_frame"]) != dict(frame):
                record["consumed"] = True
                raise InteractionContractError(
                    "observation_frame_changed",
                    "the observation coordinate frame changed",
                    observation_id=observation_id,
                    expected_frame=record["coordinate_frame"],
                    actual_frame=dict(frame),
                )
        record["consumed"] = True
        return self.public(record)

    def invalidate_all(self) -> None:
        for record in self._records.values():
            record["consumed"] = True


@dataclass(frozen=True)
class TargetMatch:
    target: dict[str, Any]
    match_type: str
    score: float
    alternatives: tuple[dict[str, Any], ...]


def _candidate_summary(
    element: Mapping[str, Any], match_type: str, score: float
) -> dict[str, Any]:
    summary = {
        "name": str(element.get("name", "")),
        "role": str(element.get("role", "")),
        "match_type": match_type,
        "score": score,
        "visible": bool(element.get("visible", True)),
    }
    if "rect_px" in element:
        summary["rect_px"] = list(element["rect_px"])
    return summary


def select_target(
    query: str,
    elements: Iterable[Mapping[str, Any]],
    *,
    exact: bool = False,
    min_score: float = 0.8,
) -> TargetMatch:
    """Select one deterministic exact-first target or reject ambiguity/weakness."""

    name_query, separator, role_query = query.partition(":")
    name_norm = name_query.strip().casefold()
    role_norm = role_query.strip().casefold() if separator else ""
    if not name_norm:
        raise InteractionContractError("target_query_invalid", "target query is empty")

    ranked: list[tuple[float, str, dict[str, Any]]] = []
    for raw in elements:
        element = dict(raw)
        name = str(element.get("name", "")).strip()
        role = str(element.get("role", ""))
        if role_norm and role_norm not in role.casefold():
            continue
        normalized = name.casefold()
        if normalized == name_norm:
            match_type, score = "exact", 1.0
        elif normalized.startswith(name_norm):
            match_type, score = "prefix", 0.85
        elif name_norm in normalized:
            match_type, score = "contains", 0.6
        else:
            continue
        ranked.append((score, match_type, element))

    if not ranked:
        raise InteractionContractError(
            "target_not_found", "no UI element matches the requested name", query=query
        )
    ranked.sort(
        key=lambda item: (
            -item[0],
            not bool(item[2].get("visible", True)),
            str(item[2].get("name", "")).casefold(),
            str(item[2].get("role", "")).casefold(),
        )
    )
    best_score = ranked[0][0]
    best_tier = [item for item in ranked if item[0] == best_score]
    if len(best_tier) != 1:
        raise InteractionContractError(
            "ambiguous_target",
            "multiple UI elements share the strongest match",
            query=query,
            candidates=[
                _candidate_summary(element, match_type, score)
                for score, match_type, element in best_tier
            ],
        )

    score, match_type, target = best_tier[0]
    alternatives = tuple(
        _candidate_summary(element, candidate_type, candidate_score)
        for candidate_score, candidate_type, element in ranked[1:6]
    )
    if exact and match_type != "exact":
        raise InteractionContractError(
            "exact_target_required",
            "no exact UI element name match exists",
            query=query,
            candidates=[_candidate_summary(target, match_type, score), *alternatives],
        )
    if score < float(min_score):
        raise InteractionContractError(
            "low_confidence_target",
            "the strongest UI element match is below the minimum confidence",
            query=query,
            minimum_score=float(min_score),
            candidates=[_candidate_summary(target, match_type, score), *alternatives],
        )
    return TargetMatch(
        target=target,
        match_type=match_type,
        score=score,
        alternatives=alternatives,
    )


def send_text_verified(
    text: str,
    *,
    send_chunk: Callable[[str], int],
    check_focus: Callable[[], Mapping[str, Any]],
    expected_window: Mapping[str, Any] | None = None,
    chunk_chars: int = 100,
) -> dict[str, Any]:
    """Send text in bounded chunks with focus checks and exact char accounting."""

    requested = len(text)
    chunk_size = max(1, min(int(chunk_chars), 1_000))
    expected = describe_window(expected_window) if expected_window is not None else None
    sent = 0
    segments = 0
    last_focus: dict[str, Any] | None = None

    def _result(
        *, status: str, code: str | None = None, reason: str | None = None,
        actual: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        complete = status == "complete"
        out: dict[str, Any] = {
            "requested_chars": requested,
            "sent_chars": sent,
            "complete": complete,
            "partial": not complete and sent > 0,
            "status": status,
            "segments": segments,
            "target_focus": actual or last_focus,
        }
        if code:
            out["code"] = code
        if reason:
            out["reason"] = reason
        if expected is not None:
            out["expected_window"] = expected
        if actual is not None:
            out["actual_window"] = describe_window(actual)
        return out

    starts = range(0, requested, chunk_size) if requested else (0,)
    for start in starts:
        try:
            actual = describe_window(check_focus())
        except InteractionContractError as exc:
            return _result(
                status="partial" if sent else "rejected",
                code=exc.code,
                reason=exc.reason,
                actual=exc.details.get("actual_window"),
            )
        except Exception as exc:  # noqa: BLE001 - structured fail-closed boundary
            return _result(
                status="partial" if sent else "rejected",
                code="focus_check_failed",
                reason=f"could not verify target focus: {type(exc).__name__}: {exc}",
            )
        last_focus = actual
        if expected is not None and not _same_window(expected, actual):
            return _result(
                status="partial" if sent else "rejected",
                code="foreground_window_mismatch",
                reason="foreground window changed before the next text segment",
                actual=actual,
            )
        chunk = text[start : start + chunk_size]
        if not chunk:
            break
        try:
            accepted = int(send_chunk(chunk))
        except Exception as exc:  # noqa: BLE001 - report, never hide partial delivery
            return _result(
                status="partial" if sent else "rejected",
                code="text_send_failed",
                reason=f"text segment failed: {type(exc).__name__}: {exc}",
                actual=actual,
            )
        accepted = max(0, min(accepted, len(chunk)))
        sent += accepted
        segments += 1
        if accepted != len(chunk):
            return _result(
                status="partial" if sent else "rejected",
                code="short_write",
                reason="input backend accepted fewer characters than requested",
                actual=actual,
            )

    return _result(status="complete", actual=last_focus)
