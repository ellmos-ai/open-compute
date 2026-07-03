"""Configuration for the agent loop.

A single dataclass describes the backend choice, scope (browser vs OS), display
geometry, and safety settings. It can be built in code or loaded from a JSON
dict / file. No hard-coded paths; everything is explicit.

Pure standard library.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Config:
    """Runtime configuration.

    Args:
        backend: ``mock`` (default, no SDK), ``claude``, or ``openai``.
        scope: ``browser`` or ``os`` -- selects which driver interface the loop
            expects. ``browser`` is the safer default.
        model: Backend model identifier. Defaults are set per backend if left
            ``None``.
        display_width: Display width in pixels (denormalization basis).
        display_height: Display height in pixels.
        safety_mode: One of ``confirm`` / ``allow_all`` / ``read_only``
            (see :class:`open_compute.safety.SafetyPolicy`).
        max_steps: Hard cap on agent-loop iterations.
        always_foreground: When ``True`` (or ``OC_ALWAYS_FOREGROUND=1`` env
            var), ``oc do --ensure-foreground`` and ``oc run --ensure-foreground``
            will activate the target window before *every* action, not only when
            it is detected to be in the background.
        extra: Free-form backend-specific options (e.g. API base URL).
    """

    backend: str = "mock"
    scope: str = "browser"
    model: str | None = None
    display_width: int = 1280
    display_height: int = 800
    safety_mode: str = "confirm"
    max_steps: int = 20
    always_foreground: bool = field(
        default_factory=lambda: bool(os.environ.get("OC_ALWAYS_FOREGROUND", ""))
    )
    extra: dict[str, Any] = field(default_factory=dict)
    clirec: dict[str, Any] = field(default_factory=lambda: {
        "ringbuffer_enabled": False,
        "ringbuffer_minutes": 15,
        "capture_screenshots": True,
        "mask_password_fields": True,
        "pause_hotkey": "ctrl+alt+p",  # reserved: global pause hotkey not yet wired (see ROADMAP.md)
        "recordings_dir": "recordings",
    })

    _VALID_BACKENDS = ("mock", "claude", "openai")
    _VALID_SCOPES = ("browser", "os")

    def __post_init__(self) -> None:
        if self.backend not in self._VALID_BACKENDS:
            raise ValueError(
                f"backend must be one of {self._VALID_BACKENDS}, got {self.backend!r}"
            )
        if self.scope not in self._VALID_SCOPES:
            raise ValueError(
                f"scope must be one of {self._VALID_SCOPES}, got {self.scope!r}"
            )
        if self.display_width <= 0 or self.display_height <= 0:
            raise ValueError("display dimensions must be positive")
        if self.max_steps <= 0:
            raise ValueError("max_steps must be positive")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        """Build a :class:`Config` from a plain dict (ignores unknown keys).

        The ``clirec`` sub-dict is shallow-merged over defaults so that callers
        only need to specify the keys they want to override.
        """
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clirec_override = data.get("clirec") if isinstance(data.get("clirec"), dict) else None
        filtered = {k: v for k, v in data.items() if k in known and k != "clirec"}
        cfg = cls(**filtered)
        if clirec_override is not None:
            cfg.clirec = {**cfg.clirec, **clirec_override}
        return cfg

    @classmethod
    def from_json(cls, path: str | Path) -> "Config":
        """Load configuration from a JSON file."""
        text = Path(path).read_text(encoding="utf-8")
        return cls.from_dict(json.loads(text))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict."""
        return asdict(self)


def clirec_recorder_config(cfg: "Config"):
    """Map ``cfg.clirec`` to a ``clirec.recorder.RecorderConfig``.

    The import is deferred because clirec is an optional external package.
    """
    from clirec.config import recorder_config_from_dict

    return recorder_config_from_dict(cfg.clirec)
