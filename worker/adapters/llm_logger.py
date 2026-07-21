"""Log every LLM API call to llm_usage_log for superadmin cost monitoring.

Usage — call after any LLM invocation:

    from adapters.llm_logger import log_llm_usage
    log_llm_usage(db, provider="anthropic", model="claude-haiku-4-5-20251001",
                  operation="classify_topics", input_tokens=1200, output_tokens=800,
                  cost_usd=0.0012, duration_ms=3200, scan_id=scan_id, client_id=client_id)
"""

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Anthropic pricing (per 1M tokens) — not in api_pricing.py which is OpenAI/Gemini only
ANTHROPIC_PRICING = {
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-sonnet-4-6":        {"input": 3.00, "output": 15.00},
    "claude-opus-4-6":          {"input": 15.00, "output": 75.00},
    # Legacy
    "claude-3-5-haiku-20241022": {"input": 0.80, "output": 4.00},
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
}

# Models missing from the submodule's api_pricing.py (which we never edit) -
# checked BEFORE the submodule so the daily/BYOK caps don't go blind on them
# (unknown model = cost 0 there). Per 1M tokens.
# Source : ai.google.dev/gemini-api/docs/pricing (July 2026).
# `cached_input` (per 1M) = the price of a prompt-cache HIT. OpenAI bills
# cached input at ~10% of the base input rate on the GPT-5 family (prompt
# caching). Omit the key and cached tokens fall back to the full input rate
# (= pre-2026-07-21 behaviour, safe overestimate). Verify against your OpenAI
# billing dashboard and tune if a model's tier differs.
SAAS_PRICING_OVERLAY = {
    "gemini-3.5-flash": {"input": 1.50, "output": 9.00},  # Gemini: no cache tier here
    "gemini-3.1-flash-lite": {"input": 0.25, "output": 1.50},
    # Model-version selector allowlist (BYOK) - the org monthly cap must see
    # real costs when a customer selects these.
    "gpt-5.5": {"input": 5.00, "output": 30.00, "cached_input": 0.50},
    "gpt-5.6-luna": {"input": 1.00, "output": 6.00, "cached_input": 0.10},
}


def estimate_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_input_tokens: int = 0,
) -> float:
    """Estimate USD cost from tokens. Returns 0 if model unknown.

    `cached_input_tokens` is the subset of `input_tokens` served from the
    provider prompt cache. It is priced at `pricing['cached_input']` when the
    model declares one, else at the full input rate (no discount) - so any
    caller that does not pass it keeps its exact prior cost.
    """
    if provider == "anthropic":
        pricing = ANTHROPIC_PRICING.get(model)
    elif model in SAAS_PRICING_OVERLAY:
        pricing = SAAS_PRICING_OVERLAY[model]
    else:
        # Use seo_llm pricing for OpenAI/Gemini. It is cache-unaware, so cached
        # tokens bill at full rate there (small models / Gemini = negligible).
        try:
            from seo_llm.src.api_pricing import calculate_cost
            result = calculate_cost(model, input_tokens, output_tokens)
            return result["total_cost_usd"]
        except Exception:
            return 0.0

    if not pricing:
        return 0.0

    cached = max(0, min(cached_input_tokens or 0, input_tokens))
    uncached = input_tokens - cached
    cached_rate = pricing.get("cached_input", pricing["input"])
    input_cost = (uncached / 1_000_000) * pricing["input"] + (cached / 1_000_000) * cached_rate
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return round(input_cost + output_cost, 6)


def log_llm_usage(
    db: Session,
    *,
    provider: str,
    model: str,
    operation: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
    cost_usd: float | None = None,
    duration_ms: int | None = None,
    scan_id: str | None = None,
    client_id: str | None = None,
    error: bool = False,
    key_source: str = "platform",
) -> None:
    """Insert a row into llm_usage_log.

    Stays best-effort (swallows DB failure) for one specific reason : many
    callers fire this from inside an `except` block on the LLM call itself.
    Propagating a flush error there would mask the real provider error with
    a meaningless DB-side traceback.

    Changed from `logger.warning` to `logger.exception` so the failure
    surfaces in Sentry with a stack trace — the budget cap relies on these
    rows being present (Sprint 2 — services/llm_budget.py), so a silently
    dropped row is a budget-cap correctness bug.
    """
    try:
        from models import LlmUsageLog

        if cost_usd is None:
            cost_usd = estimate_cost(
                provider, model, input_tokens, output_tokens, cached_input_tokens
            )

        db.add(LlmUsageLog(
            provider=provider,
            model=model,
            operation=operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_input_tokens or 0,
            cost_usd=cost_usd,
            duration_ms=duration_ms,
            scan_id=scan_id,
            client_id=client_id,
            error=error,
            # BYOK (migration 060) - 'platform' | 'byok'. The org monthly cap
            # (services/byok.py) counts ONLY 'byok' rows, so the default keeps
            # every unmodified caller correct.
            key_source=key_source,
        ))
        db.flush()
    except Exception:
        logger.exception(
            "Failed to log LLM usage — budget cap may underestimate today's spend"
        )
