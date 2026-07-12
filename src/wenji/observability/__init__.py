"""Read-only observability surface for wenji.

Exposes :func:`compute_stats` (corpus + index aggregation) and
:func:`compute_segment_trace` (query pipeline trace). Used by the
``/api/stats`` and ``/api/segment`` HTTP routes plus the matching
``wenji stats`` / ``wenji segment`` CLI commands.
"""

from __future__ import annotations

from wenji.observability.segment import (
    SegmentTrace,
    TokenInfo,
    compute_segment_trace,
)
from wenji.observability.stats import IndicesInfo, StatsResult, compute_stats

__all__ = [
    "compute_stats",
    "compute_segment_trace",
    "StatsResult",
    "IndicesInfo",
    "SegmentTrace",
    "TokenInfo",
]
