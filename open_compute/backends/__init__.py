"""Model backends behind a single protocol, plus a factory.

Every concrete backend implements :class:`ComputerBackend`: given an
:class:`~open_compute.perception.Observation` and the running message history, it
returns the next batch of canonical :class:`~open_compute.actions.Action` objects
(or signals that the task is done).

Vendor SDKs (`anthropic`, `openai`) are imported **lazily** inside the concrete
backend constructors -- importing this package, or any submodule, never requires
an SDK to be installed. Only instantiating a real backend touches its SDK.
"""

from .base import BackendResult, ComputerBackend
from .factory import get_backend
from .mock import MockBackend

__all__ = ["ComputerBackend", "BackendResult", "MockBackend", "get_backend"]
