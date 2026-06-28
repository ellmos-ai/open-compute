import pytest
from open_compute.actions import Action, ActionType
from open_compute.perception import Observation
from open_compute.clirec.format import Recording, Step
from open_compute.clirec.replay import replay


class FakeExec:
    width = 1000
    height = 500
    def __init__(self, fail_points=None):
        self.executed = []
        self._fail_points = fail_points or set()
    def execute(self, action: Action) -> Observation:
        # Fail if this normalized x is configured to fail
        if action.x is not None and round(action.x, 3) in self._fail_points:
            raise RuntimeError("element not found")
        self.executed.append(action)
        return Observation(screenshot=None, width=self.width, height=self.height)


def test_dumb_replay_executes_all_steps():
    rec = Recording("t", "now", "H", "1000x500", steps=[
        Step(1, 0.0, "click", x=500, y=250, btn="left"),
        Step(2, 0.1, "type", text="hi"),
        Step(3, 0.2, "key", keys="enter"),
    ])
    ex = FakeExec()
    rep = replay(rec, ex)
    assert rep.total == 3 and rep.ok == 3 and rep.failures == []
    assert ex.executed[0].type == ActionType.LEFT_CLICK
    assert abs(ex.executed[0].x - 0.5) < 1e-6  # 500/1000
    assert ex.executed[1].type == ActionType.TYPE and ex.executed[1].text == "hi"
    assert ex.executed[2].type == ActionType.KEY


def test_param_substitution_in_replay():
    rec = Recording("t", "now", "H", "1000x500", steps=[
        Step(1, 0.0, "type", text="${msg}")])
    ex = FakeExec()
    replay(rec, ex, params={"msg": "hello"})
    assert ex.executed[0].text == "hello"


def test_adaptive_fallback_used_when_dumb_fails():
    rec = Recording("t", "now", "H", "1000x500", steps=[
        Step(1, 0.0, "click", x=500, y=250, btn="left")])
    ex = FakeExec(fail_points={0.5})  # dumb (0.5) fails
    rep = replay(rec, ex, locate=lambda step: (0.8, 0.8))  # relocated
    assert rep.ok == 1 and rep.fallbacks == 1 and rep.failures == []
    assert abs(ex.executed[-1].x - 0.8) < 1e-6


def test_failure_recorded_when_both_paths_fail():
    rec = Recording("t", "now", "H", "1000x500", steps=[
        Step(1, 0.0, "click", x=500, y=250, btn="left")])
    ex = FakeExec(fail_points={0.5, 0.8})
    rep = replay(rec, ex, locate=lambda step: (0.8, 0.8))
    assert rep.ok == 0 and len(rep.failures) == 1
