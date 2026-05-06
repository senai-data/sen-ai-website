"""Worker utility helpers shared across handlers/adapters."""

import logging

logger = logging.getLogger(__name__)


def max_tokens_for(model: str | None, cap: int | None = None) -> int:
    """Return the optimal max output-tokens for a given model.

    Ported from seo-llm/src/segment_analyzer.py:get_max_tokens_for_model. Update this
    when new model versions ship with different output limits. The `cap` arg lets
    callers limit to expected output size when the model can do more than needed
    (e.g. a 4k-token JSON shouldn't request 65k from Gemini 2.5).

    Returns the model's max output capacity if `cap` is None, else min(model_max, cap).
    Unknown models fall back to 8192.
    """
    if not model:
        base = 8192
    else:
        m = model.lower()
        if "gpt-5" in m:
            base = 32000
        elif "gpt-4" in m or m.startswith("o"):
            base = 16384
        elif "gemini-3" in m or "gemini-2.5" in m:
            base = 65536
        elif "gemini-2.0" in m or "gemini-1.5" in m:
            base = 8192
        elif "gemini" in m:
            base = 8192
        elif "claude" in m:
            # Claude 4.x family — Sonnet/Opus support 64K extended; default API limit 16K
            base = 16384
        else:
            base = 8192
    return min(base, cap) if cap else base
