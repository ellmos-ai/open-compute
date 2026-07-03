"""Compatibility wrapper for ``clirec.format``."""

try:
    from clirec.format import *  # noqa: F401,F403
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "Install the optional `clirec` package to use open_compute.clirec.format."
    ) from exc
