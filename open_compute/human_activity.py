"""Headless human-activity classification for cooperative control.

The module intentionally does not install hooks, poll in a background thread,
or record keys/buttons.  The optional Windows adapter performs a single
``GetLastInputInfo`` query when explicitly called.  Tests inject callables, so
the native API is never touched by the test suite.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Callable


_DWORD_MODULUS = 2**32


def _tick(value: int) -> int:
    if value < 0:
        raise ValueError("tick values must be non-negative")
    return int(value) % _DWORD_MODULUS


def _elapsed_ms(later: int, earlier: int) -> int:
    """Return unsigned-DWORD elapsed milliseconds, including wraparound."""

    return (_tick(later) - _tick(earlier)) % _DWORD_MODULUS


class InputProvenance(str, Enum):
    HUMAN = "human"
    AGENT = "agent"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LastInputSample:
    """Timestamp-only input sample.

    No concrete key, button, coordinate, process, or text is represented.
    ``device`` is deliberately coarse and defaults to ``unknown`` because
    ``GetLastInputInfo`` does not expose the device type.
    """

    observed_tick_ms: int
    last_input_tick_ms: int
    device: str = "unknown"

    def __post_init__(self) -> None:
        _tick(self.observed_tick_ms)
        _tick(self.last_input_tick_ms)
        if self.device not in {"unknown", "keyboard", "pointer"}:
            raise ValueError("device must be unknown|keyboard|pointer")

    @property
    def age_ms(self) -> int:
        return _elapsed_ms(self.observed_tick_ms, self.last_input_tick_ms)


@dataclass(frozen=True)
class InjectedInputWindow:
    action_id: str
    started_tick_ms: int
    ended_tick_ms: int

    def __post_init__(self) -> None:
        if not self.action_id.strip():
            raise ValueError("action_id must not be empty")
        _tick(self.started_tick_ms)
        _tick(self.ended_tick_ms)


@dataclass(frozen=True)
class ActivityAssessment:
    recent: bool
    provenance: InputProvenance
    age_ms: int
    device: str
    action_id: str | None = None


class HumanActivityClassifier:
    """Classify timestamp-only samples against bounded own-input windows."""

    def __init__(
        self,
        *,
        recent_threshold_ms: int = 750,
        injection_tolerance_ms: int = 25,
        max_injection_windows: int = 100,
    ) -> None:
        if not 0 <= recent_threshold_ms <= 60_000:
            raise ValueError("recent_threshold_ms must be in 0..60000")
        if not 0 <= injection_tolerance_ms <= 1_000:
            raise ValueError("injection_tolerance_ms must be in 0..1000")
        if not 1 <= max_injection_windows <= 10_000:
            raise ValueError("max_injection_windows must be in 1..10000")
        self.recent_threshold_ms = recent_threshold_ms
        self.injection_tolerance_ms = injection_tolerance_ms
        self._injected: deque[InjectedInputWindow] = deque(
            maxlen=max_injection_windows
        )

    def record_agent_input(
        self, action_id: str, started_tick_ms: int, ended_tick_ms: int
    ) -> None:
        window = InjectedInputWindow(
            action_id=action_id.strip(),
            started_tick_ms=_tick(started_tick_ms),
            ended_tick_ms=_tick(ended_tick_ms),
        )
        duration = _elapsed_ms(window.ended_tick_ms, window.started_tick_ms)
        if duration > 60_000:
            raise ValueError("injected input window must not exceed 60 seconds")
        self._injected.append(window)

    def assess(self, sample: LastInputSample) -> ActivityAssessment:
        recent = sample.age_ms <= self.recent_threshold_ms
        if not recent:
            return ActivityAssessment(
                recent=False,
                provenance=InputProvenance.UNKNOWN,
                age_ms=sample.age_ms,
                device=sample.device,
            )

        for window in reversed(self._injected):
            if self._matches_window(sample.last_input_tick_ms, window):
                return ActivityAssessment(
                    recent=True,
                    provenance=InputProvenance.AGENT,
                    age_ms=sample.age_ms,
                    device=sample.device,
                    action_id=window.action_id,
                )

        return ActivityAssessment(
            recent=True,
            provenance=InputProvenance.HUMAN,
            age_ms=sample.age_ms,
            device=sample.device,
        )

    def _matches_window(
        self, input_tick_ms: int, window: InjectedInputWindow
    ) -> bool:
        duration = _elapsed_ms(window.ended_tick_ms, window.started_tick_ms)
        from_start = _elapsed_ms(input_tick_ms, window.started_tick_ms)
        if from_start <= duration + self.injection_tolerance_ms:
            return True
        # Accept a small timestamp skew before the recorded start.
        return (
            _elapsed_ms(window.started_tick_ms, input_tick_ms)
            <= self.injection_tolerance_ms
        )


class GetLastInputInfoAdapter:
    """Single-shot, lazily resolved Windows ``GetLastInputInfo`` adapter.

    Construct with ``query_last_input_ms`` and ``query_tick_count_ms`` in tests
    and headless integrations.  Calling the default adapter is the only code
    path that touches ``user32``; no watcher or live hook is created.
    """

    def __init__(
        self,
        *,
        query_last_input_ms: Callable[[], int] | None = None,
        query_tick_count_ms: Callable[[], int] | None = None,
    ) -> None:
        if (query_last_input_ms is None) != (query_tick_count_ms is None):
            raise ValueError("provide both query callables or neither")
        self._query_last_input_ms = query_last_input_ms
        self._query_tick_count_ms = query_tick_count_ms

    def sample(self) -> LastInputSample:
        if self._query_last_input_ms is not None:
            assert self._query_tick_count_ms is not None
            return LastInputSample(
                observed_tick_ms=self._query_tick_count_ms(),
                last_input_tick_ms=self._query_last_input_ms(),
            )
        last_input, observed = self._query_win32()
        return LastInputSample(
            observed_tick_ms=observed,
            last_input_tick_ms=last_input,
        )

    @staticmethod
    def _query_win32() -> tuple[int, int]:
        import ctypes
        import sys
        from ctypes import wintypes

        if sys.platform != "win32":
            raise RuntimeError("GetLastInputInfo is Windows-only")

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.UINT),
                ("dwTime", wintypes.DWORD),
            ]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            raise OSError("GetLastInputInfo failed")
        # GetTickCount64 is used for observation, then normalized to the same
        # DWORD clock as LASTINPUTINFO.dwTime by LastInputSample.
        observed = int(ctypes.windll.kernel32.GetTickCount64())
        return int(info.dwTime), observed
