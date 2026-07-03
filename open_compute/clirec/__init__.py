"""Compatibility namespace for the external ``clirec`` package.

The implementation moved to https://github.com/ellmos-ai/clirec. Install it
with ``pip install clirec`` or ``pip install open-compute[clirec]`` before using
``open_compute.clirec`` compatibility imports or ``oc rec``.
"""

try:
    from clirec import *  # noqa: F401,F403
except ModuleNotFoundError as exc:  # pragma: no cover - exercised by users
    raise ModuleNotFoundError(
        "open_compute.clirec moved to the optional `clirec` package. "
        "Install it with `pip install clirec` or `pip install open-compute[clirec]`."
    ) from exc
