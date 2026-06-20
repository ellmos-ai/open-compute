"""Driver interfaces and the dependency-injected mock executor.

An :class:`Executor` performs canonical :class:`~open_compute.actions.Action`
objects against some target and returns an :class:`~open_compute.perception.Observation`.
:class:`BrowserDriver` and :class:`OSDriver` are scope-specific interfaces; the
:class:`MockExecutor` is a fully working, SDK-free executor that records every
action and returns synthetic observations -- the basis for dry-run and tests.

:class:`LocalExecutor` is the real Windows executor (mss + ctypes SendInput),
available when the ``open-compute[local]`` extra is installed. It is NOT
imported here at module level (preserves zero-runtime-deps for ``import
open_compute``). Import it directly::

    from open_compute.drivers.local import LocalExecutor
"""

from .base import BrowserDriver, Executor, OSDriver
from .mock import MockExecutor

__all__ = ["Executor", "BrowserDriver", "OSDriver", "MockExecutor"]
