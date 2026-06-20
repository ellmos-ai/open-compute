"""Backend factory.

Selects and constructs a :class:`~open_compute.backends.base.ComputerBackend`
from a backend name. The ``claude`` and ``openai`` modules are imported lazily
inside this function so that ``import open_compute`` never pulls in a vendor SDK;
only the chosen branch touches an SDK (and even then, only its concrete backend
constructor does).

Pure standard library at import time.
"""

from __future__ import annotations

from typing import Any

from .base import ComputerBackend
from .mock import MockBackend


def get_backend(name: str, width: int, height: int, **kwargs: Any) -> ComputerBackend:
    """Build a backend by name.

    Args:
        name: ``mock``, ``claude``, or ``openai``.
        width, height: Display dimensions to pass to the backend.
        **kwargs: Forwarded to the concrete backend constructor (e.g. ``model``,
            ``api_key``, ``client``).

    Returns:
        A :class:`ComputerBackend` instance.

    Raises:
        ValueError: For an unknown backend name.
        ImportError: If a vendor SDK is required but not installed (raised by the
            concrete backend constructor, not here).
    """
    key = name.lower()
    if key == "mock":
        return MockBackend(**kwargs)
    if key == "claude":
        from .claude import ClaudeComputerBackend  # lazy: optional SDK

        return ClaudeComputerBackend(width, height, **kwargs)
    if key == "openai":
        from .openai import OpenAIComputerBackend  # lazy: optional SDK

        return OpenAIComputerBackend(width, height, **kwargs)
    raise ValueError(f"unknown backend {name!r}; expected mock | claude | openai")
