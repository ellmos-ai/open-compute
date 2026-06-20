"""open-compute feed abstractions and built-in feeds.

Feeds are pluggable perception channels (Multi-Feed architecture).
Each feed implements the ``PerceptionFeed`` protocol and optionally
the ``Targeter`` protocol for semantic element resolution + invocation.

Imports here are kept minimal — the UIA feed is lazy (Windows-only extra).
Use ``available_feeds()`` from ``feeds.registry`` for runtime capability
detection.

Pure standard library at import time (no mss, no uiautomation).
"""

from .base import FeedObservation, PerceptionFeed, Target, Targeter

__all__ = [
    "FeedObservation",
    "PerceptionFeed",
    "Target",
    "Targeter",
]
