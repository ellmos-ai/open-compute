"""Push-to-talk voice capture (Windows, zero-dependency via winmm MCI).

The human holds a key, speaks, releases: the recorder captures microphone
audio while the key is held and writes a WAV file. Speech-to-text and the
model's spoken answer are deliberately NOT part of this slice — the WAV path
is handed to the caller (the agent) as JSON; transcription and TTS stay
model-side (backend, Ollama, or a later module).

Every side effect is injected (``mci`` sender, ``key_down`` probe, ``sleep``),
so headless tests drive the full state machine without audio hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
import sys
import time
from typing import Callable


MciSend = Callable[[str], int]
KeyDown = Callable[[int], bool]


@dataclass(frozen=True)
class TalkResult:
    recorded: bool
    path: str | None
    seconds: float
    reason: str


def record_push_to_talk(
    *,
    vk: int,
    out_path: str,
    mci: MciSend,
    key_down: KeyDown,
    sleep: Callable[[float], None] = time.sleep,
    max_seconds: float = 60.0,
    wait_timeout: float | None = None,
    poll_seconds: float = 0.03,
    alias: str = "ocptt",
) -> TalkResult:
    """Record microphone audio while ``vk`` is held; save WAV to ``out_path``.

    Waits until the key goes down (bounded by ``wait_timeout`` when set),
    records while held (hard cap ``max_seconds``), then stops, saves, and
    always closes the MCI device. A failing MCI command raises RuntimeError
    with the offending command text.
    """

    if not 0 < max_seconds <= 600:
        raise ValueError("max_seconds must be in (0, 600]")
    if not 0.005 <= poll_seconds <= 1.0:
        raise ValueError("poll_seconds must be in 0.005..1.0")

    def _send(command: str) -> None:
        rc = mci(command)
        if rc != 0:
            raise RuntimeError(f"MCI command failed ({rc}): {command}")

    waited = 0.0
    while not key_down(vk):
        if wait_timeout is not None and waited >= wait_timeout:
            return TalkResult(False, None, 0.0, "wait_timeout")
        sleep(poll_seconds)
        waited += poll_seconds

    _send(f"open new type waveaudio alias {alias}")
    held = 0.0
    try:
        _send(f"record {alias}")
        while key_down(vk) and held < max_seconds:
            sleep(poll_seconds)
            held += poll_seconds
        _send(f"stop {alias}")
        _send(f'save {alias} "{out_path}"')
    finally:
        _send(f"close {alias}")

    reason = "max_seconds" if held >= max_seconds else "released"
    return TalkResult(True, out_path, round(held, 3), reason)


def winmm_mci() -> MciSend:
    """Real MCI sender via Windows winmm (lazy, raises off-Windows)."""

    if sys.platform != "win32":
        raise RuntimeError("winmm MCI is Windows-only")
    import ctypes
    from ctypes import wintypes

    winmm = ctypes.windll.winmm
    winmm.mciSendStringW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, wintypes.HANDLE,
    ]
    winmm.mciSendStringW.restype = wintypes.UINT

    def _send(command: str) -> int:
        buf = ctypes.create_unicode_buffer(128)
        return int(winmm.mciSendStringW(command, buf, 128, None))

    return _send


def async_key_down() -> KeyDown:
    """Real key probe via GetAsyncKeyState (lazy, raises off-Windows)."""

    if sys.platform != "win32":
        raise RuntimeError("GetAsyncKeyState is Windows-only")
    import ctypes

    user32 = ctypes.windll.user32
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short

    def _down(vk: int) -> bool:
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)

    return _down
