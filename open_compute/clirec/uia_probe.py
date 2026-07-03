"""Compatibility wrapper for ``clirec.uia_probe``."""

try:
    from clirec.uia_probe import *  # noqa: F401,F403
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "Install the optional `clirec` package to use open_compute.clirec.uia_probe."
    ) from exc
