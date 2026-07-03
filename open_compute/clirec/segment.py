"""Compatibility wrapper for ``clirec.segment``."""

try:
    from clirec.segment import *  # noqa: F401,F403
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "Install the optional `clirec` package to use open_compute.clirec.segment."
    ) from exc
