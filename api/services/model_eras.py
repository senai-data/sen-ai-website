"""P3 model eras - shared era-boundary comparison for trend endpoints.

A scan's summary["models"] ({provider: model} + reserved "analyzer" key) is
written at completion by the worker and backfilled for history. Trend builders
compare consecutive emitted points with models_changed() and flag boundaries;
the UI replaces the delta with an "AI models updated" chip there instead of
letting the curve lie across a model change.
"""

from __future__ import annotations


def models_changed(prev: dict | None, curr: dict | None, provider: str | None = None) -> bool:
    """Era boundary between two consecutive trend points.

    True iff BOTH points carry a non-empty models dict and they differ
    (provider set OR version). Unknown (missing/empty) on either side =
    False - never fabricate a boundary from ignorance.

    With a provider filter the series only aggregates that provider's rows,
    so a change on another provider must NOT flag the filtered series; still
    always compare the "analyzer" entry - it re-reads every response, so it
    shifts all providers at once.
    """
    prev = prev or {}
    curr = curr or {}
    if not prev or not curr:
        return False
    if provider and provider != "all":
        keys = (provider, "analyzer")
        return any(prev.get(k) != curr.get(k) for k in keys)
    return prev != curr
