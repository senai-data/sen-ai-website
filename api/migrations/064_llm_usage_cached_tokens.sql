-- 064_llm_usage_cached_tokens.sql
--
-- Prompt-cache accuracy for LLM cost (reconciliation 2026-07-21).
--
-- estimate_cost (worker/adapters/llm_logger.py) billed the FULL input rate on
-- the whole input_tokens count, ignoring prompt caching. OpenAI bills cached
-- input tokens at a fraction of the base rate, so on cache-heavy models
-- (gpt-5.6-luna) the SaaS OVERESTIMATED cost by +2% (little cache) to +9.5%
-- (7.5M cached tokens on a 51M-token day). Safe direction for the BYOK cap
-- (never underestimates) but it drifts up as clients rescan and cache-hit
-- rates climb.
--
-- Store the cached slice so cost can be split cached vs uncached and the org
-- monthly cap / cost display stop over-counting.
--
-- Additive + idempotent: existing rows inherit 0 (= no cache = current
-- behaviour), so the deploy order after this migration cannot break anything.

ALTER TABLE llm_usage_log
    ADD COLUMN IF NOT EXISTS cached_input_tokens integer NOT NULL DEFAULT 0;

COMMENT ON COLUMN llm_usage_log.cached_input_tokens IS
    'Subset of input_tokens served from the provider prompt cache, billed at '
    'the cached rate (see SAAS_PRICING_OVERLAY[model].cached_input). 0 = no '
    'cache / provider without caching. See migration 064.';
