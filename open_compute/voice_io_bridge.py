"""Optional offline voice input through the local ellmos-voice-io component."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import uuid
from typing import Callable


VOSK_MODEL_NAME = "vosk-model-small-de-0.15"
MODEL_ENV = "OPEN_COMPUTE_VOSK_MODEL"


def default_model_path() -> Path:
    configured = os.environ.get(MODEL_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    return base / "open-compute" / "models" / VOSK_MODEL_NAME


def transcription_status() -> tuple[bool, str]:
    model_path = default_model_path()
    if not model_path.is_dir():
        return False, f"Lokales deutsches Vosk-Modell nicht gefunden: {model_path}"
    try:
        from ellmos_voice_io import SpeechToText
    except ImportError:
        return False, "ellmos-voice-io und sein optionaler Vosk-Treiber sind nicht installiert."
    try:
        available, detail = SpeechToText(engine="vosk", model_path=str(model_path)).available()
    except Exception as exc:
        return False, f"Lokale Sprachtranskription konnte nicht geladen werden: {type(exc).__name__}: {exc}"
    if not available:
        return False, f"Lokale Sprachtranskription ist nicht verfügbar: {detail}"
    return True, "Bereit: Sprache wird lokal transkribiert."


def transcribe_local_wav(audio_path: str | Path) -> str:
    """Transcribe one local WAV file; never downloads a model or sends audio away."""
    ready, detail = transcription_status()
    if not ready:
        raise RuntimeError(detail)
    from ellmos_voice_io import SpeechToText

    recognizer = SpeechToText(engine="vosk", model_path=str(default_model_path()))
    return recognizer.transcribe_file(audio_path, language="de")


class MicrophoneRecording:
    """Small start/stop wrapper around open-compute's existing Windows MCI capture."""

    def __init__(
        self,
        *,
        mci_factory: Callable[[], Callable[[str], int]] | None = None,
        temp_dir: str | Path | None = None,
    ) -> None:
        self.mci_factory = mci_factory
        self.temp_dir = Path(temp_dir) if temp_dir else Path(tempfile.gettempdir())
        self.mci: Callable[[str], int] | None = None
        self.alias: str | None = None
        self.path: Path | None = None

    def start(self) -> Path:
        if self.alias is not None:
            raise RuntimeError("a microphone recording is already active")
        if self.mci_factory is None:
            if sys.platform != "win32":
                raise RuntimeError("Mikrofonaufnahme ist auf diesem System nicht verfügbar.")
            from .talk import winmm_mci

            factory = winmm_mci
        else:
            factory = self.mci_factory
        mci = factory()
        alias = f"occhat{uuid.uuid4().hex[:10]}"
        path = self.temp_dir / f"open-compute-chat-audio-{uuid.uuid4().hex}.wav"
        rc = mci(f"open new type waveaudio alias {alias}")
        if rc != 0:
            raise RuntimeError(f"Mikrofon konnte nicht geöffnet werden (MCI {rc}).")
        self.mci, self.alias, self.path = mci, alias, path
        rc = mci(f"record {alias}")
        if rc != 0:
            self.cancel()
            raise RuntimeError(f"Mikrofonaufnahme konnte nicht starten (MCI {rc}).")
        return path

    def stop(self) -> Path:
        if self.mci is None or self.alias is None or self.path is None:
            raise RuntimeError("es läuft gerade keine Mikrofonaufnahme")
        path = self.path
        mci, alias = self.mci, self.alias
        error: Exception | None = None
        try:
            rc = mci(f"stop {alias}")
            if rc != 0:
                raise RuntimeError(f"Mikrofonaufnahme konnte nicht gestoppt werden (MCI {rc}).")
            rc = mci(f'save {alias} "{path}"')
            if rc != 0:
                raise RuntimeError(f"WAV-Datei konnte nicht gespeichert werden (MCI {rc}).")
            if not path.is_file() or path.stat().st_size <= 44:
                raise RuntimeError("Die Aufnahme enthält keine Audiodaten.")
        except Exception as exc:
            error = exc
        finally:
            try:
                mci(f"close {alias}")
            except OSError:
                pass
            self.mci = self.alias = self.path = None
        if error is not None:
            path.unlink(missing_ok=True)
            raise error
        return path

    def cancel(self) -> Path | None:
        path = self.path
        if self.mci is not None and self.alias is not None:
            try:
                self.mci(f"stop {self.alias}")
            except OSError:
                pass
            try:
                self.mci(f"close {self.alias}")
            except OSError:
                pass
        self.mci = self.alias = self.path = None
        if path is not None:
            path.unlink(missing_ok=True)
        return path
