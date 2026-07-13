"""Learning layer: action-outcome logging, Bandit/Bayes weighting, profiles.

Architecture
------------

**ActionOutcome logging**
  Every action result is logged as ``(feed_used, app, action_type) → success``.
  Stored in ``_state/outcomes.jsonl`` (one JSON line per outcome).

**Bandit / Bayes weighting**
  Per ``(app, feed, action_type)`` triple we maintain a Beta-distribution
  posterior ``(alpha, beta)`` with a Bernoulli likelihood:
    - success  → alpha += 1
    - failure  → beta  += 1
  Starting prior: ``alpha=1, beta=1`` (uniform / Laplace smoothing).
  Expected success-rate: ``alpha / (alpha + beta)``.

  This is a closed-form, deterministic update — no random sampling needed,
  making it fully unit-testable without seeds.

  Weights are persisted to ``_state/weights.json`` and loaded at startup
  (warmstart).

**Use-Case-Profiles**
  A profile maps a ``(program, usecase)`` key to a dict of successful
  ``{feed_name: dosage_mode}`` overrides.  Stored in
  ``_state/profiles.json``, loaded at startup.

  FeedManager.set_dosage() can be driven from a loaded profile, giving the
  warmstart behaviour described in ARCHITECTURE.md.

**Cross-Session LESSONS-LEARNED**
  Textual lessons that persist across sessions.  Stored as a JSONL log at
  ``_state/lessons.jsonl`` (one JSON line per lesson).

  Format: ``{"ts": <float>, "lesson": "<text>", "tags": [...]}``.

  At startup the last N lessons are loaded and made available as
  ``LearningManager.lessons``.

Zero-dependency guarantee
-------------------------
Pure standard library at import time.  No NumPy, SciPy, or other extras.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# _state directory helper (shared with feed_manager)
# ---------------------------------------------------------------------------

def _state_dir() -> pathlib.Path:
    env = os.environ.get("OC_STATE_DIR", "")
    if env:
        d = pathlib.Path(env)
    else:
        d = pathlib.Path(__file__).resolve().parent.parent / "_state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_text_atomic(path: pathlib.Path, text: str) -> None:
    """Atomically replace *path* with *text*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f"{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


def _trim_jsonl_tail(path: pathlib.Path, keep: int) -> None:
    """Keep only the newest *keep* JSONL rows in *path*."""
    if not path.exists():
        return
    if keep <= 0:
        _write_text_atomic(path, "")
        return

    tail: list[str] = []
    line_count = 0
    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line_count += 1
            tail.append(raw_line)
            if len(tail) > keep:
                tail.pop(0)

    if line_count > keep:
        _write_text_atomic(path, "".join(tail))


# ---------------------------------------------------------------------------
# Outcome dataclass
# ---------------------------------------------------------------------------

@dataclass
class ActionOutcome:
    """One logged action result.

    Attributes:
        feed_used:    Name of the perception feed that was active (e.g.
                      ``"uia_windows"``).
        app:          Application name / window title substring that was
                      targeted (e.g. ``"word"``, ``"explorer"``).
        action_type:  Canonical action type string (e.g. ``"left_click"``,
                      ``"invoke"``).
        success:      True if the action achieved its goal; False otherwise.
        ts:           Unix timestamp (float).
        note:         Optional free-text note (e.g. ``"UIA-Invoke unreliable"``).
    """

    feed_used: str
    app: str
    action_type: str
    success: bool
    ts: float = field(default_factory=time.time)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "feed_used": self.feed_used,
            "app": self.app,
            "action_type": self.action_type,
            "success": self.success,
            "ts": self.ts,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ActionOutcome":
        return cls(
            feed_used=d.get("feed_used", ""),
            app=d.get("app", ""),
            action_type=d.get("action_type", ""),
            success=bool(d.get("success", False)),
            ts=float(d.get("ts", 0.0)),
            note=d.get("note", ""),
        )


# ---------------------------------------------------------------------------
# Bandit / Bayes weight update (pure, deterministic)
# ---------------------------------------------------------------------------

@dataclass
class BetaPrior:
    """Beta-distribution posterior for a single (app, feed, action_type) triple.

    Prior: Uniform (alpha=1, beta=1) = Laplace smoothing.
    Update rule (Bernoulli likelihood):
        success → alpha += 1
        failure → beta  += 1
    Expected success rate: alpha / (alpha + beta).
    """

    alpha: float = 1.0
    beta: float = 1.0

    def update(self, success: bool) -> None:
        """Update in-place given an observed outcome."""
        if success:
            self.alpha += 1.0
        else:
            self.beta += 1.0

    @property
    def expected_rate(self) -> float:
        """Expected success probability (0..1)."""
        total = self.alpha + self.beta
        if total <= 0:
            return 0.5
        return self.alpha / total

    def to_dict(self) -> dict[str, float]:
        return {"alpha": self.alpha, "beta": self.beta}

    @classmethod
    def from_dict(cls, d: dict) -> "BetaPrior":
        return cls(alpha=float(d.get("alpha", 1.0)), beta=float(d.get("beta", 1.0)))


def _weight_key(app: str, feed: str, action_type: str) -> str:
    """Canonical string key for the weights dict."""
    return f"{app}|{feed}|{action_type}"


# ---------------------------------------------------------------------------
# Lesson dataclass
# ---------------------------------------------------------------------------

@dataclass
class Lesson:
    """One cross-session lesson.

    Attributes:
        lesson: Human-readable lesson text (e.g.
                ``"App Z: UIA-Invoke unreliable → use pixel click"``).
        tags:   Optional list of tags (app name, feed, action type).
        ts:     Unix timestamp of when the lesson was recorded.
    """

    lesson: str
    tags: list[str] = field(default_factory=list)
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"lesson": self.lesson, "tags": self.tags, "ts": self.ts}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Lesson":
        return cls(
            lesson=d.get("lesson", ""),
            tags=list(d.get("tags", [])),
            ts=float(d.get("ts", 0.0)),
        )


# ---------------------------------------------------------------------------
# LearningManager
# ---------------------------------------------------------------------------

class LearningManager:
    """Manages action-outcome logging, Bandit/Bayes weighting, profiles, lessons.

    Usage
    -----
    >>> from open_compute.learning import LearningManager
    >>> mgr = LearningManager()
    >>> mgr.log_outcome("uia_windows", "word", "invoke", success=True)
    >>> rate = mgr.success_rate("uia_windows", "word", "invoke")
    >>> mgr.add_lesson("App Z: UIA-Invoke unreliable", tags=["word", "invoke"])
    >>> profile = mgr.load_profile("word", "editing")
    >>> mgr.save_profile("word", "editing", {"uia_windows": "delta"})

    All state is persisted to ``_state/`` (gitignored, local).

    Args:
        state_dir:             Override for the state directory path (used in tests).
        max_lessons:           Maximum number of recent lessons loaded at startup.
        max_outcomes_history:  Maximum number of JSONL outcome rows kept on disk.
        max_lessons_history:   Maximum number of JSONL lesson rows kept on disk.
                               Defaults to ``max(max_lessons, 1000)``.
    """

    def __init__(
        self,
        state_dir: str | pathlib.Path | None = None,
        max_lessons: int = 100,
        max_outcomes_history: int = 5000,
        max_lessons_history: int | None = None,
    ) -> None:
        if state_dir is not None:
            self._state_dir = pathlib.Path(state_dir)
            self._state_dir.mkdir(parents=True, exist_ok=True)
        else:
            self._state_dir = _state_dir()

        self._max_lessons = max_lessons
        self._max_outcomes_history = max(0, int(max_outcomes_history))
        if max_lessons_history is None:
            max_lessons_history = max(max_lessons, 1000)
        self._max_lessons_history = max(0, int(max_lessons_history))

        # In-memory weight table: key → BetaPrior
        self._weights: dict[str, BetaPrior] = {}

        # In-memory profile store: (program, usecase) → {feed: dosage}
        self._profiles: dict[str, dict[str, str]] = {}

        # Recent lessons (in-memory; persisted to JSONL)
        self.lessons: list[Lesson] = []

        # Load persisted state
        self._load_weights()
        self._load_profiles()
        self._load_lessons()

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def _outcomes_path(self) -> pathlib.Path:
        return self._state_dir / "outcomes.jsonl"

    def _weights_path(self) -> pathlib.Path:
        return self._state_dir / "weights.json"

    def _profiles_path(self) -> pathlib.Path:
        return self._state_dir / "profiles.json"

    def _lessons_path(self) -> pathlib.Path:
        return self._state_dir / "lessons.jsonl"

    # ------------------------------------------------------------------
    # Outcome logging
    # ------------------------------------------------------------------

    def log_outcome(
        self,
        feed_used: str,
        app: str,
        action_type: str,
        success: bool,
        note: str = "",
    ) -> ActionOutcome:
        """Log one action outcome and update the Bandit/Bayes weight.

        Args:
            feed_used:    Feed name (e.g. ``"uia_windows"``).
            app:          App/window name substring.
            action_type:  Canonical action type string.
            success:      True = goal achieved; False = failure.
            note:         Optional free-text note.

        Returns:
            The created :class:`ActionOutcome` instance.
        """
        outcome = ActionOutcome(
            feed_used=feed_used,
            app=app,
            action_type=action_type,
            success=success,
            note=note,
        )

        # Append to JSONL file
        try:
            with self._outcomes_path().open("a", encoding="utf-8") as f:
                f.write(json.dumps(outcome.to_dict(), ensure_ascii=False) + "\n")
            _trim_jsonl_tail(self._outcomes_path(), self._max_outcomes_history)
        except Exception:  # noqa: BLE001
            pass  # best-effort; in-memory update still happens

        # Update Bandit/Bayes weight
        key = _weight_key(app, feed_used, action_type)
        if key not in self._weights:
            self._weights[key] = BetaPrior()
        self._weights[key].update(success)

        # Persist updated weights
        self._save_weights()

        return outcome

    # ------------------------------------------------------------------
    # Weight access
    # ------------------------------------------------------------------

    def success_rate(self, feed: str, app: str, action_type: str) -> float:
        """Return the expected success rate for (feed, app, action_type).

        Returns the Beta-posterior mean.  If no data is recorded yet, returns
        the prior mean of 0.5 (uniform Beta(1,1)).

        Args:
            feed:        Feed name.
            app:         App/window name.
            action_type: Action type string.

        Returns:
            Float in [0, 1].
        """
        key = _weight_key(app, feed, action_type)
        prior = self._weights.get(key, BetaPrior())
        return prior.expected_rate

    def best_feed(
        self,
        app: str,
        action_type: str,
        candidates: list[str],
    ) -> str | None:
        """Return the feed with the highest expected success rate for (app, action_type).

        Used to bias dosage selection / warmstart feed preferences.

        Args:
            app:         App/window name.
            action_type: Action type.
            candidates:  List of feed names to rank.

        Returns:
            Name of the best-ranked feed, or None if ``candidates`` is empty.
        """
        if not candidates:
            return None
        return max(candidates, key=lambda f: self.success_rate(f, app, action_type))

    # ------------------------------------------------------------------
    # Weight persistence
    # ------------------------------------------------------------------

    def _save_weights(self) -> None:
        try:
            data = {k: v.to_dict() for k, v in self._weights.items()}
            _write_text_atomic(
                self._weights_path(),
                json.dumps(data, ensure_ascii=False, indent=2),
            )
        except Exception:  # noqa: BLE001
            pass

    def _load_weights(self) -> None:
        path = self._weights_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for k, v in data.items():
                self._weights[k] = BetaPrior.from_dict(v)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Use-Case Profiles
    # ------------------------------------------------------------------

    def _profile_key(self, program: str, usecase: str) -> str:
        return f"{program}|{usecase}"

    def save_profile(
        self,
        program: str,
        usecase: str,
        dosage_map: dict[str, str],
    ) -> None:
        """Persist a successful feed+dosage combination for (program, usecase).

        Args:
            program:    Application/program name (e.g. ``"word"``).
            usecase:    Use-case label (e.g. ``"text_editing"``).
            dosage_map: ``{feed_name: dosage_mode}`` that proved successful.
        """
        key = self._profile_key(program, usecase)
        self._profiles[key] = dict(dosage_map)
        self._save_profiles()

    def load_profile(
        self, program: str, usecase: str
    ) -> dict[str, str] | None:
        """Load a persisted profile for (program, usecase).

        Returns the dosage_map dict, or None if no profile is stored.

        Args:
            program:  Application/program name.
            usecase:  Use-case label.
        """
        key = self._profile_key(program, usecase)
        return self._profiles.get(key)

    def list_profiles(self) -> list[dict[str, Any]]:
        """Return all stored profiles as a list of dicts."""
        result = []
        for key, dosage_map in self._profiles.items():
            parts = key.split("|", 1)
            program = parts[0] if len(parts) > 0 else ""
            usecase = parts[1] if len(parts) > 1 else ""
            result.append({"program": program, "usecase": usecase, "dosage_map": dosage_map})
        return result

    def _save_profiles(self) -> None:
        try:
            _write_text_atomic(
                self._profiles_path(),
                json.dumps(self._profiles, ensure_ascii=False, indent=2),
            )
        except Exception:  # noqa: BLE001
            pass

    def _load_profiles(self) -> None:
        path = self._profiles_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._profiles = data
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Cross-Session LESSONS-LEARNED
    # ------------------------------------------------------------------

    def add_lesson(self, lesson: str, tags: list[str] | None = None) -> Lesson:
        """Append a textual lesson to the cross-session LESSONS-LEARNED log.

        Args:
            lesson: Human-readable lesson text.
            tags:   Optional tags (e.g. ``["word", "invoke", "uia_windows"]``).

        Returns:
            The created :class:`Lesson`.
        """
        l = Lesson(lesson=lesson, tags=list(tags or []))
        self.lessons.append(l)
        # Trim in-memory list
        if len(self.lessons) > self._max_lessons:
            self.lessons = self.lessons[-self._max_lessons:]

        # Append to JSONL
        try:
            with self._lessons_path().open("a", encoding="utf-8") as f:
                f.write(json.dumps(l.to_dict(), ensure_ascii=False) + "\n")
            _trim_jsonl_tail(self._lessons_path(), self._max_lessons_history)
        except Exception:  # noqa: BLE001
            pass

        return l

    def _load_lessons(self) -> None:
        """Load the most recent max_lessons from the JSONL file."""
        path = self._lessons_path()
        if not path.exists():
            return
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            loaded: list[Lesson] = []
            for line in lines:
                line = line.strip()
                if line:
                    try:
                        loaded.append(Lesson.from_dict(json.loads(line)))
                    except Exception:  # noqa: BLE001
                        pass
            self.lessons = loaded[-self._max_lessons:]
        except Exception:  # noqa: BLE001
            pass

    def get_lessons(self, tag: str | None = None) -> list[Lesson]:
        """Return lessons, optionally filtered by tag.

        Args:
            tag: If given, only return lessons whose ``tags`` list contains
                 this value.

        Returns:
            List of :class:`Lesson` instances (newest-last order).
        """
        if tag is None:
            return list(self.lessons)
        return [l for l in self.lessons if tag in l.tags]

    # ------------------------------------------------------------------
    # Integration: apply profile dosages to a FeedManager
    # ------------------------------------------------------------------

    def apply_profile_to_manager(
        self,
        manager: Any,
        program: str,
        usecase: str,
    ) -> bool:
        """Apply a stored profile's dosage map to a FeedManager instance.

        This is the warmstart seam: on session start, load the profile for the
        active program/usecase and call ``set_dosage()`` on the manager so
        weights from prior sessions immediately bias feed selection.

        Args:
            manager:  A FeedManager instance (or any object with set_dosage()).
            program:  Application/program name.
            usecase:  Use-case label.

        Returns:
            True if a profile was found and applied; False otherwise.
        """
        profile = self.load_profile(program, usecase)
        if profile is None:
            return False
        for feed_name, dosage in profile.items():
            try:
                manager.set_dosage(feed_name, dosage)
            except Exception:  # noqa: BLE001
                pass
        return True
