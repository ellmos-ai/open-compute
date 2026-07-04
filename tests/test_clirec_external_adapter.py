"""Adapter-Tests gegen das OPTIONALE externe clirec-Paket.

clirec ist ein optionales Extra (pyproject: open-compute[clirec]) — ohne
installiertes Paket wird uebersprungen statt die Collection zu brechen.
Fuer lokale Entwicklung wird ein Sibling-Checkout (../clirec) automatisch
in sys.path aufgenommen.
"""
import sys
from pathlib import Path

import pytest

_sibling = Path(__file__).resolve().parent.parent.parent / "clirec"
if _sibling.is_dir() and str(_sibling) not in sys.path:
    sys.path.insert(0, str(_sibling))

pytest.importorskip("clirec", reason="optionales Extra clirec nicht installiert")

from clirec.replay import ReplayAction  # noqa: E402
from clirec.integrations.open_compute import OpenComputeExecutorAdapter  # noqa: E402

from open_compute.actions import ActionType  # noqa: E402


class FakeExecutor:
    width = 1000
    height = 500

    def __init__(self):
        self.executed = []

    def execute(self, action):
        self.executed.append(action)
        return None


def test_clirec_open_compute_adapter_converts_actions():
    executor = FakeExecutor()
    adapter = OpenComputeExecutorAdapter(executor)

    adapter.execute(ReplayAction("left_click", x=0.5, y=0.25))
    adapter.execute(ReplayAction("type", text="hello"))

    assert executor.executed[0].type is ActionType.LEFT_CLICK
    assert executor.executed[0].x == 0.5
    assert executor.executed[0].y == 0.25
    assert executor.executed[1].type is ActionType.TYPE
    assert executor.executed[1].text == "hello"
