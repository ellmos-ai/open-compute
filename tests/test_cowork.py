"""Behavioral contract tests; no OS input and no provider inference."""
import json
import threading

import pytest

from open_compute.cowork import Budgets, CoworkService, canonical_action, encoded
from open_compute.interaction import ObservationRegistry, describe_window


class FakeDesktop:
    def __init__(self):
        self.window = describe_window({"hwnd": 42, "pid": 7, "title": "Test editor",
                                       "foreground": True, "rect": {"left": 0, "top": 0,
                                       "width": 800, "height": 600}, "center": {"x": .5, "y": .5}})
        self.registry = ObservationRegistry()
        self.payload = {"elements": [{"name": "Document", "role": "Edit"}]}
        self.calls = []
        self.released = 0
        self.failure = None
        self.started = threading.Event()
        self.block = False
        self.release_failure = False

    def windows(self):
        return [self.window]

    def observe(self, target):
        meta = self.registry.record(kind="fake", payload=self.payload, window=self.window,
                                    frame={"left": 0, "top": 0, "width": 800, "height": 600})
        return {**meta, "semantics": json.loads(encoded(self.payload))}

    def validate(self, obs, target):
        if target["window_token"] != self.window["window_token"]:
            raise PermissionError("window changed")
        try:
            self.registry.claim(obs["observation_id"], payload=self.payload, window=self.window)
        except Exception as exc:
            raise PermissionError(str(exc)) from exc

    def execute(self, action, target, observation, cancel):
        self.started.set()
        if self.block:
            assert cancel.wait(3), "test expected cancellation"
        if self.failure:
            raise self.failure
        self.calls.append(action)
        return {"status": "uncertain", "dispatch": "executed"}

    def release_all(self):
        self.released += 1
        if self.release_failure:
            raise RuntimeError("release failed")

    def lens(self, target):
        return {"png": "aGVsbG8=", "width": 1, "height": 1, "local_only": True}


@pytest.fixture
def setup():
    backend = FakeDesktop()
    service = CoworkService(backend, mode="confirm", _grace_seconds=0, work_mode="confirm")
    key = service._ui_key
    service.snapshot(human_key=key)
    service.configure(key, "Ziel äöü", "Neueste Einschränkung: nichts löschen", backend.window["window_token"])
    return service, backend, key


def offer(setup, action=None):
    s, b, key = setup
    s.message(key, "Bitte helfen", "human-1")
    turn = s.next_turn("Host / model-test")
    obs = s.model_observe()
    s.reply(turn["context"]["turn_id"], turn["claim"], "Mein Vorschlag",
                     {"action": action or {"type": "type", "text": "Hallo äöü 🙂"},
                      "observation_id": obs["observation_id"]})
    assert b.calls == []
    return s.snapshot(human_key=key)["offers"][-1]


def decide(s, key, o, accept=True, **overrides):
    args = {"offer_id": o["id"], "accept": accept, "nonce": o["approval_nonce"],
            "revision": o["revision"], "argument_digest": o["digest"]}
    args.update(overrides)
    return s.decide(key, **args)


def test_real_model_path_and_single_use_approval(setup):
    s, b, key = setup
    o = offer(setup)
    result = decide(s, key, o)
    assert result["status"] == "uncertain"
    assert len(b.calls) == 1
    assert b.calls[0].text == "Hallo äöü 🙂"
    assert not s.executing and s.control.lease is None
    with pytest.raises(PermissionError):
        decide(s, key, o)
    assert len(b.calls) == 1


@pytest.mark.parametrize("tamper", [{"nonce": "forged"}, {"revision": 999}, {"argument_digest": "forged"}])
def test_tampered_approvals_never_dispatch(setup, tamper):
    s, b, key = setup
    o = offer(setup)
    with pytest.raises(PermissionError):
        decide(s, key, o, **tamper)
    assert not b.calls


@pytest.mark.parametrize("kind", ["type", "key", "scroll", "mouse_move", "left_click"])
def test_read_only_blocks_every_synthetic_input(setup, kind):
    s, b, key = setup
    s.policy.mode = "read_only"
    action = {"type": kind}
    if kind in {"type", "key"}:
        action["text"] = "a"
    else:
        action.update(x=.5, y=.5)
    if kind == "scroll":
        action.update(scroll_direction="down", scroll_amount=1)
    o = offer(setup, action)
    assert decide(s, key, o)["status"] == "denied"
    assert not b.calls


def test_reject_and_model_never_gets_ui_capability(setup):
    s, b, key = setup
    o = offer(setup)
    model_view = encoded(s.model_status())
    assert key not in model_view and o["approval_nonce"] not in model_view
    assert "approval_nonce" not in encoded(s.snapshot())
    assert decide(s, key, o, False)["status"] == "rejected"
    assert not b.calls


@pytest.mark.parametrize("change", ["observe", "human", "window", "payload", "pause", "expiry"])
def test_changed_state_blocks_old_offer(setup, change):
    s, b, key = setup
    o = offer(setup)
    if change == "observe":
        s.observe()
    elif change == "human":
        s.message(key, "Neue Korrektur", "human-2")
    elif change == "window":
        b.window = describe_window({**b.window, "pid": 999, "process_id": 999})
    elif change == "payload":
        b.payload = {"elements": [{"name": "Different"}]}
    elif change == "pause":
        s.interrupt(key)
    else:
        s.clock = lambda: o["expires"] + 1
    try:
        result = decide(s, key, o)
        assert result["status"] == "aborted"
    except PermissionError:
        pass
    assert not b.calls


def test_ambiguous_dispatch_not_retried(setup):
    s, b, key = setup
    o = offer(setup)
    b.failure = RuntimeError("dispatch outcome unknown")
    result = decide(s, key, o)
    assert result["status"] == "uncertain"
    with pytest.raises(PermissionError):
        decide(s, key, o)
    assert b.released >= 1 and not s.executing


def test_stop_during_execution_releases_and_prevents_resume(setup):
    s, b, key = setup
    o = offer(setup)
    b.block = True
    result = []
    thread = threading.Thread(target=lambda: result.append(decide(s, key, o)))
    thread.start()
    assert b.started.wait(2)
    s.interrupt(key, stop=True)
    thread.join(3)
    assert not thread.is_alive() and not s.executing
    assert result[0]["status"] == "uncertain"
    assert s.control.lease is None and b.released >= 2
    with pytest.raises(PermissionError):
        s.resume(key)


def test_cleanup_failure_still_releases_authority(setup):
    s, b, key = setup
    o = offer(setup)
    b.release_failure = True
    result = decide(s, key, o)
    assert result["status"] == "uncertain"
    assert not s.executing and s.control.lease is None


def test_one_queue_dedupe_idle_and_output_budget(setup):
    s, b, key = setup
    for _ in range(5):
        assert s.next_turn("model")["status"] == "idle"
    s.message(key, "🙂", "same-id")
    assert s.message(key, "🙂", "same-id")["status"] == "duplicate"
    turn = s.next_turn("model")
    assert s.next_turn("model")["status"] == "busy"
    with pytest.raises(ValueError):
        s.reply(turn["context"]["turn_id"], turn["claim"], "🙂" * 1201)
    s.reply(turn["context"]["turn_id"], turn["claim"], "🙂" * 1200)
    assert s.metrics["output_characters"] == 1200
    assert s.metrics["delivered_turns"] == 1


def test_latest_constraints_protected_and_history_bounded(setup):
    s, b, key = setup
    s.budgets = Budgets(context=4000, recent=6)
    for i in range(105):
        s.metrics["delivered_turns"] = 0
        s.message(key, f"{i}:" + "🙂" * 1100, str(i))
        turn = s.next_turn("model")
        context = turn["context"]
        assert len(encoded(context)) <= 4000
        assert context["constraints"] == "Neueste Einschränkung: nichts löschen"
        assert context["recent_messages"][-1]["text"].startswith(f"{i}:")
        s.reply(context["turn_id"], turn["claim"], "Antwort")
    assert len(s.messages) <= 100
    assert len(encoded(s.model_status())) < 1500


def test_observation_and_lens_bounded_per_turn(setup):
    s, b, key = setup
    with pytest.raises(PermissionError):
        s.model_observe()
    s.message(key, "Bild bitte", "a")
    s.next_turn("model")
    s.model_observe()
    with pytest.raises(PermissionError):
        s.model_observe()
    s.model_lens("Semantik ist unklar")
    with pytest.raises(PermissionError):
        s.model_lens("nochmal")
    assert s.metrics["images"] == 1


def test_watchdog_revokes_pending_approval_and_resume_requires_new_offer(setup):
    s, b, key = setup
    o = offer(setup)
    now = s.clock()
    s.clock = lambda: now + 9
    s.watchdog()
    assert s.control.mode.value == "paused"
    s.snapshot(human_key=key)
    s.resume(key)
    with pytest.raises(PermissionError):
        decide(s, key, o)
    assert not b.calls


@pytest.mark.parametrize("raw", [{"type": "key_down", "text": "ctrl"},
                                 {"type": "launch_app", "app_name": "cmd"},
                                 {"type": "type", "text": "x", "approved": True},
                                 {"type": "left_click", "x": float("nan"), "y": .5}])
def test_unknown_fields_holds_and_nonfinite_coordinates_rejected(raw):
    with pytest.raises((ValueError, TypeError)):
        canonical_action(raw)


def test_discuss_without_windows_or_target():
    class NoDesktop:
        def windows(self):
            pytest.fail("discussion must not read the desktop")
        def release_all(self):
            pass
    s = CoworkService(NoDesktop())
    key = s._ui_key
    s.configure(key, "Nur besprechen", "Text ist ausreichend", "")
    s.message(key, "Hallo", "a")
    turn = s.next_turn("keyless host")
    assert turn["context"]["work_mode"] == "human_leads"
    assert turn["context"]["target"] is None
    with pytest.raises((PermissionError, ValueError)):
        s.observe()
    with pytest.raises((PermissionError, ValueError)):
        s.reply(turn["context"]["turn_id"], turn["claim"], "Aktion",
                {"action": {"type": "key", "text": "a"}, "observation_id": "fake"})
    assert s.reply(turn["context"]["turn_id"], turn["claim"], "Nur Text")["status"] == "replied"


def test_human_leads_keeps_explicit_individual_adoption(setup):
    s, b, key = setup
    s.set_work_mode(key, "observe")
    o = offer(setup)
    assert decide(s, key, o)["status"] == "uncertain"
    assert len(b.calls) == 1 and s.model_status()["work_mode"] == "human_leads"


def test_mode_switch_revokes_pending_offer(setup):
    s, b, key = setup
    o = offer(setup)
    s.set_work_mode(key, "human_only")
    s.set_work_mode(key, "human_leads")
    with pytest.raises(PermissionError):
        decide(s, key, o)
    assert not b.calls


def test_mode_switch_interrupts_active_execution(setup):
    s, b, key = setup
    o = offer(setup)
    b.block = True
    result = []
    thread = threading.Thread(target=lambda: result.append(decide(s, key, o)))
    thread.start()
    assert b.started.wait(2)
    s.set_work_mode(key, "human_only")
    thread.join(3)
    assert not thread.is_alive() and not s.executing
    assert result[0]["status"] == "uncertain"
    assert s.control.lease is None and s.work_mode == "human_only" and s.target is None


def test_turn_expiry_does_not_leave_queue_stuck(setup):
    s, b, key = setup
    s.message(key, "Frage", "a")
    now = s.clock()
    s.clock = lambda: now + s.budgets.model_seconds + 1
    s.watchdog()
    assert s.turn is None
    assert s.messages[-1]["role"] == "system"


def test_maximum_context_with_three_realistic_native_receipts_stays_claimable(setup):
    s, b, key = setup
    b.window = describe_window({**b.window, "title": "W" * 350})
    s.configure(key, "Z" * 350, "E" * 1200, b.window["window_token"])
    window = dict(b.window)
    def native_execute(action, target, observation, cancel):
        b.calls.append(action)
        return {"status": "uncertain", "dispatch": "executed",
                "reason": "Eingabe ausgeführt. Bitte das Ergebnis im Zielfenster prüfen.",
                "text_transfer": {"requested_chars": 350, "sent_chars": 350,
                    "complete": True, "partial": False, "status": "complete", "segments": 44,
                    "target_focus": window, "expected_window": window, "actual_window": window}}
    b.execute = native_execute
    for index in range(3):
        s.message(key, "🙂" * 1200, f"native-{index}")
        turn = s.next_turn("Host")
        obs = s.model_observe()
        s.reply(turn["context"]["turn_id"], turn["claim"], "A" * 1200,
                {"action": {"type": "type", "text": "T" * 350}, "observation_id": obs["observation_id"]})
        o = s.snapshot(human_key=key)["offers"][-1]
        assert decide(s, key, o)["status"] == "uncertain"
    s.observe()
    s.message(key, "N" * 1200, "latest")
    turn = s.next_turn("Host")
    context = turn["context"]
    assert len(encoded(context)) <= s.budgets.context
    assert context["constraints"] == "E" * 1200
    assert context["recent_messages"][-1]["text"] == "N" * 1200
    assert "target_focus" not in encoded(context["offers"])
    assert all(x["result"]["sent_chars"] == 350 for x in context["offers"])
