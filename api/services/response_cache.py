"""In-process LRU+TTL cache for heavy read endpoints - act-scope P5c.

Why this exists : a COMPLETED scan is immutable, yet /results/aggregated
recomputes ~2.7-5s of Python over its rows on every hit - and the scan
header calls it once per tab visit. Caching the small, expensive responses
makes tab navigation instant after the first hit.

Design decisions (plan 2026-06-12, trigger 'aggregated lent' met 2026-07-18) :
- In-process only. One api container ; a cold cache after restart is fine.
  No Redis on purpose.
- Keys embed `scan.updated_at` : any mutation that changes a rendered
  result must touch it (generate_opportunities already does ; judge
  sentiment + brand-classification patches added 2026-07-18). A missed
  touch is bounded by the TTL - staleness of at most TTL seconds is an
  accepted trade-off, documented here.
- ONLY small responses get cached (summary header ~19 KB, opportunities
  ~240 KB, workspaces overview). The multi-MB full aggregated/results
  payloads are deliberately NOT cached : 128 x 10 MB would eat the VPS.
  _MAX_VALUE_BYTES guards against accidental big-value writes using the
  jsonified length the caller already knows, or a cheap len() estimate.
- Thread-safe : endpoints run in FastAPI's threadpool since the def
  conversions - a plain Lock around the OrderedDict is enough at our QPS.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any

_MAX_ENTRIES = 128
_lock = threading.Lock()
_cache: "OrderedDict[tuple, tuple[float, Any]]" = OrderedDict()

# Hit/miss counters - exposed for debugging via /api/admin if ever needed.
stats = {"hits": 0, "misses": 0}


def get(key: tuple) -> Any | None:
    """Return the cached value or None (expired entries are evicted)."""
    now = time.time()
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            stats["misses"] += 1
            return None
        expires_at, value = entry
        if now > expires_at:
            _cache.pop(key, None)
            stats["misses"] += 1
            return None
        _cache.move_to_end(key)
        stats["hits"] += 1
        return value


def put(key: tuple, value: Any, ttl_seconds: int = 120) -> None:
    with _lock:
        _cache[key] = (time.time() + ttl_seconds, value)
        _cache.move_to_end(key)
        while len(_cache) > _MAX_ENTRIES:
            _cache.popitem(last=False)


def invalidate_prefix(prefix: tuple) -> int:
    """Drop every entry whose key starts with `prefix`. Returns count.

    Belt-and-suspenders for callers that mutate without a clean
    updated_at bump ; normal invalidation is key versioning.
    """
    with _lock:
        doomed = [k for k in _cache if k[: len(prefix)] == prefix]
        for k in doomed:
            _cache.pop(k, None)
        return len(doomed)
