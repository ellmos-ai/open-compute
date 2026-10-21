import threading
import time

import pytest

from open_compute.cowork import CoworkService
from open_compute.cowork_tasks import grant_action
from test_cowork import FakeDesktop


class ScriptWorker:
    available = True
    identity = "deterministic test worker"
    last_usage = None
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.contexts = []
        self.cancelled = 0
        self.started = threading.Event()
        self.block = False
    def reason(self, context, cancel):
        self.contexts.append(context)
        self.started.set()
        if self.block:
            assert cancel.wait(3)
            raise PermissionError("cancelled")
        return next(self.outputs)
    def cancel(self): self.cancelled += 1
    def close(self): self.cancel()


class TaskDesktop(FakeDesktop):
    def execute(self, action, target, observation, cancel):
        self.calls.append(action)
        self.payload = {"elements": [{"name": action.text or action.type.value}], "content_readable": True}
        return {"status": "uncertain", "dispatch": "executed", "text_transfer": {
                "requested_chars": len(action.text or ""), "sent_chars": len(action.text or ""),
                "complete": True, "partial": False}}


def task_setup(outputs, role="model_alone", **kwargs):
    backend = TaskDesktop()
    worker = ScriptWorker(outputs)
    s = CoworkService(backend, work_mode=role, worker=worker, _grace_seconds=0, **kwargs)
    key = s._ui_key
    s.snapshot(human_key=key)
    s.configure(key, "Verbessere den Text", "Nur dieses Fenster", backend.window["window_token"])
    return s, backend, worker, key


def start(s, key, **overrides):
    args = dict(objective="Text verbessern", target_token=s.target["window_token"],
                allowed_actions=["type", "key"], seconds=30, max_steps=5, max_turns=6)
    args.update(overrides)
    return s.tasks.start_task(key, **args)


def finished(s):
    deadline = time.monotonic() + 4
    while s.tasks.running and time.monotonic() < deadline:
        time.sleep(.01)
    assert not s.tasks.running
    return s.tasks.report


def test_real_task_queue_multistep_uncertain_never_promoted(tmp_path):
    s, b, w, key = task_setup([
        {"text": "Erster Schritt", "action": {"type": "type", "text": "A"}},
        {"text": "Zweiter Schritt", "action": {"type": "type", "text": "B"}},
        {"text": "Modell meldet fertig", "done": True}], report_path=tmp_path / "reports.json")
    try:
        start(s, key)
        report = finished(s)
        assert [a.text for a in b.calls] == ["A", "B"]
        assert report["steps"] == 2 and report["turns"] == 3
        assert report["status"] == "model_reported_done" and report["semantic_verified"] is False
        assert all(x["status"] == "uncertain" for x in report["effects"])
        assert len({x["observation"]["observation_id"] for x in w.contexts}) == 3
        previous = w.contexts[1]["last_result"][0]
        assert previous["status"] == "uncertain" and previous["dispatch"] == "executed"
        assert previous["semantic_verified"] is False
        assert previous["text_transfer"] == {"requested_chars": 1, "sent_chars": 1, "complete": True, "partial": False}
        assert "different next step" in w.contexts[1]["instructions"]
        resumed = CoworkService(TaskDesktop(), report_path=tmp_path / "reports.json")
        assert not resumed.tasks.running and resumed.control.lease is None
        assert resumed.tasks.reports[-1]["status"] == "model_reported_done"
        with pytest.raises(PermissionError):
            s.resume("forged")
        s.resume(key)
        assert not s.tasks.running and len(b.calls) == 2  # resume never replays
    finally:
        s.tasks.close()


def test_human_only_blocks_all_model_and_observation_calls():
    s, b, w, key = task_setup([], role="human_leads")
    try:
        s.set_work_mode(key, "human_only")
        assert s.message(key, "Meine lokale Notiz", "x")["status"] == "saved_local"
        with pytest.raises(PermissionError):
            s.next_turn("rogue model")
        with pytest.raises(PermissionError):
            s.observe()
        time.sleep(.4)
        assert not w.contexts and not b.calls
    finally:
        s.tasks.close()


@pytest.mark.parametrize("keytext", ["Win+r", "alt+tab", "ctrl+esc", "alt+f4", "f1"])
def test_task_keys_cannot_switch_system_or_windows(keytext):
    with pytest.raises(PermissionError):
        grant_action({"type": "key", "text": keytext}, ("key",))


def test_unchanged_state_same_action_is_not_replayed():
    s, b, w, key = task_setup([{"text": "A", "action": {"type": "key", "text": "enter"}}] * 3)
    original = b.payload
    def execute(action, target, observation, cancel):
        b.calls.append(action)
        b.payload = original
        return {"status": "uncertain", "dispatch": "executed"}
    b.execute = execute
    try:
        start(s, key)
        assert finished(s)["status"] == "needs_human"
        assert len(b.calls) == 1
    finally:
        s.tasks.close()


@pytest.mark.parametrize("mode", ["read_only", "denied_type"])
def test_ceiling_and_deny_remain_authoritative(mode):
    s, b, w, key = task_setup([{"text": "A", "action": {"type": "type", "text": "A"}}])
    try:
        if mode == "read_only":
            s.policy.mode = "read_only"
            with pytest.raises(PermissionError):
                start(s, key)
        else:
            from open_compute.actions import ActionType
            s.policy.denied_actions = frozenset({ActionType.TYPE})
            start(s, key)
            assert finished(s)["status"] == "aborted"
        assert not b.calls
    finally:
        s.tasks.close()


@pytest.mark.parametrize("role,expected_cancel", [("model_leads", True), ("model_alone", False)])
def test_ui_presence_is_required_only_for_supervised_task(role, expected_cancel):
    s, b, w, key = task_setup([], role=role)
    w.block = True
    try:
        start(s, key)
        assert w.started.wait(2)
        now = s.clock()
        s.clock = lambda: now + 9
        s.watchdog()
        assert s.tasks.cancel.is_set() is expected_cancel
        if not expected_cancel:
            s.interrupt(key)
        finished(s)
        assert not b.calls and w.cancelled >= 1
    finally:
        s.tasks.close()


def test_human_leads_unchanged_state_does_not_trigger_inference_loop():
    s, b, w, key = task_setup([{"text": "Ich begleite dich."}], role="human_leads")
    try:
        deadline = time.monotonic() + 2
        while not w.contexts and time.monotonic() < deadline:
            time.sleep(.01)
        assert len(w.contexts) == 1
        s.tasks.last_human_poll = -100
        s.tasks.wake.set()
        time.sleep(.6)
        assert len(w.contexts) == 1 and not b.calls
    finally:
        s.tasks.close()
