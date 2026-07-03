"""Compatibility wrapper for ``clirec.capture.winapi``."""

try:
    from clirec.capture.winapi import *  # noqa: F401,F403
except ModuleNotFoundError as exc:  # pragma: no cover
    raise ModuleNotFoundError(
        "Install the optional `clirec` package to use open_compute.clirec.capture.winapi."
    ) from exc
