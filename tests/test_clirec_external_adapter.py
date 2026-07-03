from clirec.replay import ReplayAction
from clirec.integrations.open_compute import OpenComputeExecutorAdapter

from open_compute.actions import ActionType


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
