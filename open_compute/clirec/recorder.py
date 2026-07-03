"""Compatibility wrapper for ``clirec.recorder``."""

try:
    from clirec.recorder import *  # noqa: F401,F403
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "Install the optional `clirec` package to use open_compute.clirec.recorder."
    ) from exc
