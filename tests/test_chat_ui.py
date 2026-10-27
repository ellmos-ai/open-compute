"""Contract tests for the native host-model chat bridge."""

import os
import sys
import time
from pathlib import Path
import tempfile
from types import SimpleNamespace

import pytest

from open_compute.chat_ui import (
    COLLABORATION_PROMPTS,
    ChatBridge,
    ChatSession,
    HumanTurn,
    insert_collaboration_prompt,
)
from open_compute.voice_io_bridge import (
    MODEL_ENV,
    MicrophoneRecording,
    transcribe_local_wav,
    transcription_status,
)


class _PromptEntry:
    def __init__(self, selection=()):
        self.selection = selection
        self.operations = []

    def tag_ranges(self, _tag):
        return self.selection

    def delete(self, *args):
        self.operations.append(("delete", *args))

    def insert(self, *args):
        self.operations.append(("insert", *args))

    def focus_set(self):
        self.operations.append(("focus",))


class _FakeMci:
    def __init__(self):
        self.commands = []

    def __call__(self, command):
        self.commands.append(command)
        if command.startswith("save "):
            path = Path(command.split('"')[1])
            path.write_bytes(b"RIFF" + b"0" * 64)
        return 0


def _mci_factory(fake):
    return lambda: fake


def test_collaboration_prompts_are_inserted_for_editing_without_sending():
    assert [label for label, _ in COLLABORATION_PROMPTS] == [
        "Kurz weg", "Wieder da", "Hilfe und Vorschläge",
    ]
    entry = _PromptEntry()

    insert_collaboration_prompt(entry, COLLABORATION_PROMPTS[0][1])

    assert entry.operations == [
        ("insert", "insert", COLLABORATION_PROMPTS[0][1]),
        ("focus",),
    ]


def test_collaboration_prompt_replaces_selected_text_in_editable_field():
    entry = _PromptEntry(selection=("sel.first", "sel.last"))

    insert_collaboration_prompt(entry, "Bearbeite diesen Vorschlag")

    assert entry.operations == [
        ("delete", "sel.first", "sel.last"),
        ("insert", "insert", "Bearbeite diesen Vorschlag"),
        ("focus",),
    ]


def test_microphone_recording_saves_one_temporary_wav_and_closes_device(tmp_path):
    mci = _FakeMci()
    recorder = MicrophoneRecording(mci_factory=_mci_factory(mci), temp_dir=tmp_path)

    path = recorder.start()
    assert not path.exists()
    recorded_path = recorder.stop()

    assert recorded_path == path
    assert recorded_path.read_bytes().startswith(b"RIFF")
    assert any(command.startswith("record ") for command in mci.commands)
    assert any(command.startswith("save ") for command in mci.commands)
    assert any(command.startswith("close ") for command in mci.commands)
    recorded_path.unlink()


def test_microphone_cancel_closes_device_and_deletes_temporary_audio(tmp_path):
    mci = _FakeMci()
    recorder = MicrophoneRecording(mci_factory=_mci_factory(mci), temp_dir=tmp_path)
    path = recorder.start()

    assert recorder.cancel() == path
    assert not path.exists()
    assert recorder.alias is None
    assert any(command.startswith("close ") for command in mci.commands)


def test_chat_session_only_cleans_its_temporary_audio_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    session = ChatSession(chat_id="temp-audio-cleanup")
    owned = tmp_path / "open-compute-chat-audio-owned.wav"
    other = tmp_path / "user-audio.wav"
    owned.write_bytes(b"temporary")
    other.write_bytes(b"preserve")

    session._track_temp_audio(str(owned))
    session._track_temp_audio(str(other))
    session._cleanup_temp_audio()

    assert not owned.exists()
    assert other.read_bytes() == b"preserve"


def test_voice_io_bridge_uses_the_configured_local_model_and_german(tmp_path, monkeypatch):
    model = tmp_path / "vosk"
    model.mkdir()
    monkeypatch.setenv(MODEL_ENV, str(model))
    audio = tmp_path / "spoken.wav"
    audio.write_bytes(b"local audio")
    calls = []

    class FakeSpeechToText:
        def __init__(self, *, engine, model_path):
            calls.append(("init", engine, Path(model_path)))

        def available(self):
            return True, "vosk"

        def transcribe_file(self, path, *, language):
            calls.append(("transcribe", Path(path), language))
            return "Das ist ein lokaler Test."

    monkeypatch.setitem(
        sys.modules, "ellmos_voice_io", SimpleNamespace(SpeechToText=FakeSpeechToText)
    )

    assert transcription_status() == (True, "Bereit: Sprache wird lokal transkribiert.")
    assert transcribe_local_wav(audio) == "Das ist ein lokaler Test."
    assert calls[-1] == ("transcribe", audio, "de")


def _bridge_with_pending_turn() -> tuple[ChatBridge, ChatSession]:
    bridge = ChatBridge()
    session = ChatSession(chat_id="stable-chat")
    bridge._sessions[session.chat_id] = session
    session.turns.put(HumanTurn("Ja, bitte", "Kurz", False, 0))
    return bridge, session


def test_assistant_reply_roundtrip_and_duplicate_turn_replay():
    bridge, session = _bridge_with_pending_turn()

    _, first, timed_out, replayed = bridge.reply(
        session.chat_id,
        assistant_message="Ich kann helfen.",
        assistant_turn_id="host-turn-1",
        choices=("Weiter",),
        timeout=0.1,
    )
    _, retry, retry_timeout, retry_replayed = bridge.reply(
        session.chat_id,
        assistant_message="Ich kann helfen.",
        assistant_turn_id="host-turn-1",
        choices=("Weiter",),
        timeout=0.1,
    )

    assert first == retry == HumanTurn("Ja, bitte", "Kurz", False, 0)
    assert timed_out is retry_timeout is False
    assert replayed is False
    assert retry_replayed is True
    assert session.commands.qsize() == 1
    assert session.commands.get_nowait() == ("assistant", "Ich kann helfen.", ("Weiter",))


def test_reused_turn_id_with_different_content_is_rejected():
    bridge, session = _bridge_with_pending_turn()
    bridge.reply(
        session.chat_id, assistant_message="Erste Antwort",
        assistant_turn_id="same-id", timeout=0.1,
    )

    try:
        bridge.reply(
            session.chat_id, assistant_message="Andere Antwort",
            assistant_turn_id="same-id", timeout=0.1,
        )
    except ValueError as exc:
        assert "different content" in str(exc)
    else:
        raise AssertionError("changed content under a reused turn ID must fail")


def test_timeout_then_poll_delivers_a_later_human_turn():
    bridge, session = _bridge_with_pending_turn()
    session.turns.get_nowait()

    _, absent, timed_out = bridge.poll(session.chat_id, timeout=0.001)
    assert absent is None
    assert timed_out is True

    session.turns.put(HumanTurn("Weiter bitte", "Brainstorming", False))
    _, later, timed_out = bridge.poll(session.chat_id, timeout=0.1)
    assert later == HumanTurn("Weiter bitte", "Brainstorming", False)
    assert timed_out is False


def test_close_releases_waiting_turn_and_forgets_session():
    bridge, session = _bridge_with_pending_turn()
    assert bridge.close(session.chat_id) is True
    assert session.closed.is_set()
    assert session.next_turn(timeout=0.1) == (None, False)
    assert bridge.close(session.chat_id) is False


@pytest.mark.skipif(
    sys.platform != "win32" and not os.environ.get("DISPLAY"),
    reason="native Tk process smoke requires a desktop display",
)
def test_native_tk_child_starts_and_shuts_down_cleanly():
    session = ChatSession(chat_id="pytest-native-window")
    session.start(title="open-compute Chat Test", context="Startprüfung", choices=("Ja",))
    try:
        assert session.process is not None and session.process.poll() is None
        session.add_assistant("Antwort des Hostmodells", ("Weiter",))
        time.sleep(0.2)
        session._send_command({"type": "close"})
        session.process.wait(timeout=5)
        assert session.process.poll() is not None
    finally:
        if session.process is not None and session.process.poll() is None:
            session.process.terminate()
            session.process.wait(timeout=2)
