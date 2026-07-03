"""Compatibility wrapper for ``clirec.capture.mock``."""

try:
    from clirec.capture.mock import *  # noqa: F401,F403
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "Install the optional `clirec` package to use open_compute.clirec.capture.mock."
    ) from exc
