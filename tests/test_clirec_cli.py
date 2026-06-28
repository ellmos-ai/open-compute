"""Tests for 'oc rec' CLI sub-command (Task 8: CLI wiring for clirec)."""
import os
from open_compute.clirec.format import Recording, Step, write
from open_compute import cli


def _mk(tmp_path):
    rec = Recording("t", "now", "H", "1000x500", steps=[
        Step(1, 0.0, "type", text="${msg}")])
    p = os.path.join(tmp_path, "f.clirec")
    write(rec, p)
    return p


def test_rec_validate_ok(tmp_path, capsys):
    p = _mk(tmp_path)
    cli.cmd_rec(["validate", p])
    assert "OK" in capsys.readouterr().out


def test_rec_list(tmp_path, capsys):
    _mk(tmp_path)
    cli.cmd_rec(["list", "--dir", str(tmp_path)])
    assert "f.clirec" in capsys.readouterr().out


def test_run_replay_with_fake_executor(tmp_path):
    p = _mk(tmp_path)

    class FakeExec:
        width = 1000; height = 500
        def __init__(self): self.executed = []
        def execute(self, a):
            from open_compute.perception import Observation
            self.executed.append(a)
            return Observation(None, 1000, 500)

    ex = FakeExec()
    rep = cli._run_replay(p, {"msg": "hello"}, ex)
    assert rep.ok == 1 and ex.executed[0].text == "hello"
