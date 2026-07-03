"""Compatibility wrapper for ``clirec.replay``."""

try:
    from clirec.replay import *  # noqa: F401,F403
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "Install the optional `clirec` package to use open_compute.clirec.replay."
    ) from exc
