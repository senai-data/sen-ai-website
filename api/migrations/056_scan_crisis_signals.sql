-- 056_scan_crisis_signals.sql
--
-- Sprint 12 (Crisis Monitoring) - feature #8 from
-- project_10_action_features.md. Per-scan snapshot of negative sentiment
-- signals against the focus brand AND competitors. Mines the existing
-- scan_llm_results.brand_mentions[].sentiment field (no LLM, no fetch).
--
-- v1 is single-scan only (no cross-scan trend / no 3-sigma anomaly). The
-- value is making negative LLM mentions visible per brand so the user
-- can triage immediately. Cross-scan trend = S12.1 once enough rescans
-- accumulate per brand.
--
-- One row per (scan, brand). Rows persist for every brand that has at
-- least one mention - my_brand + competitor classifications only. The
-- per-brand severity_label + recommended playbook_category drive UI sort.
--
-- top_contexts JSONB shape :
--   [
--     {
--       "contexte": "...",
--       "sentiment_justification": "...",
--       "question": "...",
--       "question_id": "...",
--       "provider": "openai|gemini|claude",
--       "slr_id": "...",
--       "category": "safety|efficacy|pricing|ingredients|service|quality|other"
--     }, ...
--   ]
--   Capped at 5 entries, ordered by category severity then contexte length.
--
-- category_breakdown JSONB shape :
--   {"safety": 3, "efficacy": 5, "pricing": 1, ...}
--   Count of negative mentions per category. dominant_category surfaces
--   the modal one.
--
-- topic_clusters JSONB shape :
--   [{"topic_id": "...", "topic_name": "...", "negative_count": N}, ...]
--   Top 5 topics by negative mention count. Joined with scan_topics.
--
-- shared_with JSONB shape (target brand only) :
--   [{"competitor_brand_name": "...", "shared_topics": [{topic_name, n_negative_both}], "shared_questions": [...]}, ...]
--   Topics or questions where the target AND a competitor BOTH had
--   negative mentions = industry-wide signal (not target-only crisis).

CREATE TABLE IF NOT EXISTS scan_crisis_signals (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id                UUID NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    brand_id               UUID NOT NULL REFERENCES client_brands(id) ON DELETE CASCADE,
    brand_classification   TEXT NOT NULL,
    brand_name             TEXT NOT NULL,
    negative_count         INTEGER NOT NULL DEFAULT 0,
    positive_count         INTEGER NOT NULL DEFAULT 0,
    neutral_count          INTEGER NOT NULL DEFAULT 0,
    total_mentions         INTEGER NOT NULL DEFAULT 0,
    negative_ratio         REAL,
    severity               INTEGER,
    severity_label         TEXT,
    dominant_category      TEXT,
    category_breakdown     JSONB NOT NULL DEFAULT '{}'::jsonb,
    top_contexts           JSONB NOT NULL DEFAULT '[]'::jsonb,
    topic_clusters         JSONB NOT NULL DEFAULT '[]'::jsonb,
    shared_with            JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at             TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE (scan_id, brand_id),
    CONSTRAINT scs_brand_classification CHECK (
      brand_classification IN ('my_brand', 'competitor')
    ),
    CONSTRAINT scs_severity_label CHECK (
      severity_label IS NULL OR severity_label IN ('none', 'low', 'medium', 'high', 'critical')
    ),
    CONSTRAINT scs_severity_range CHECK (
      severity IS NULL OR (severity >= 0 AND severity <= 100)
    )
);

CREATE INDEX IF NOT EXISTS idx_scs_scan          ON scan_crisis_signals(scan_id);
CREATE INDEX IF NOT EXISTS idx_scs_scan_severity ON scan_crisis_signals(scan_id, severity DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_scs_scan_class    ON scan_crisis_signals(scan_id, brand_classification);

COMMENT ON TABLE scan_crisis_signals IS
    'Sprint 12 crisis monitoring snapshot. One row per (scan, brand) where '
    'brand is classified my_brand or competitor on this scan. Counts negative '
    'brand_mentions, categorizes (safety / efficacy / pricing / ingredients / '
    'service / quality / other) via multilingual keyword heuristics, clusters '
    'by scan_topic, flags shared crises (target AND competitor both negative '
    'on same topic = industry-wide). v1 is single-scan ; cross-scan trend '
    'detection = S12.1. See worker/handlers/build_crisis_radar.py + '
    'project_10_action_features.md #8.';

COMMENT ON COLUMN scan_crisis_signals.severity IS
    'Composite 0-100. 40 pts negative_count (log-saturated), 30 pts '
    'negative_ratio (linear), 15 pts safety/ingredient categories (high '
    'consequence), 15 pts dispersion (more distinct questions/providers = '
    'broader exposure). severity_label is the bucket : 0-15 none / 16-35 low '
    '/ 36-60 medium / 61-80 high / 81-100 critical.';
