"""Compatibility wrapper for ``clirec.capture.pynput_backend``."""

try:
    from clirec.capture.pynput_backend import *  # noqa: F401,F403
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "Install the optional `clirec` package to use open_compute.clirec.capture.pynput_backend."
    ) from exc
