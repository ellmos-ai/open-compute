"""open-compute: a model-agnostic computer-use core.

A small, dependency-light core for building computer-use agents that can run
Claude *and* OpenAI computer-use (and a mock) behind one interface, with a
hybrid perception layer, normalized coordinates, a canonical action schema, and
a central safety gate.

Importing this package never requires a vendor SDK -- the ``anthropic`` and
``openai`` packages are imported lazily, only when their concrete backend is
instantiated. The default wiring (mock backend + mock executor) runs fully
offline.

Quick start::

    from open_compute import AgentLoop, Config

    loop = AgentLoop(Config(backend="mock"))
    result = loop.run("Open the settings page and enable dark mode")
    print(result.done, result.steps)
"""

from __future__ import annotations

from .actions import Action, ActionType, to_claude, to_openai
from .backends import BackendResult, ComputerBackend, MockBackend, get_backend
from .config import Config
from .cooperative import (
    CooperativeOrchestrator,
    HashChainAudit,
    InstructionOrigin,
    LatchedEmergencyStop,
    ObservationDigest,
    WorkflowOutcome,
    WorkflowPlan,
)
from .coordinates import denormalize, normalize, rescale
from .drivers import Executor, MockExecutor
from .human_activity import (
    GetLastInputInfoAdapter,
    HumanActivityClassifier,
    InputProvenance,
    LastInputSample,
)
from .loop import AgentLoop, LoopResult, StepTrace
from .perception import Observation, PerceptionProvider, ScreenshotPerception
from .safety import Decision, PolicyResult, SafetyPolicy

__version__ = "0.7.0"

__all__ = [
    "Action",
    "ActionType",
    "to_claude",
    "to_openai",
    "Config",
    "CooperativeOrchestrator",
    "HashChainAudit",
    "InstructionOrigin",
    "LatchedEmergencyStop",
    "ObservationDigest",
    "WorkflowOutcome",
    "WorkflowPlan",
    "GetLastInputInfoAdapter",
    "HumanActivityClassifier",
    "InputProvenance",
    "LastInputSample",
    "normalize",
    "denormalize",
    "rescale",
    "Observation",
    "PerceptionProvider",
    "ScreenshotPerception",
    "SafetyPolicy",
    "PolicyResult",
    "Decision",
    "Executor",
    "MockExecutor",
    "ComputerBackend",
    "BackendResult",
    "MockBackend",
    "get_backend",
    "AgentLoop",
    "LoopResult",
    "StepTrace",
    "__version__",
]
