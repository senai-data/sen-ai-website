-- 042_media_publish_outcome.sql
--
-- Phase MR.1 — Suggest Alternative Media (Sprint 1, populated in Sprint 4).
--
-- Records the per-provider LLM-citation lift T+14 after a netlinking_article
-- was published on a media suggested by /suggest-media. Closes the
-- learning loop : positive lifts boost `media_catalog.llm_citation_decayed`
-- so winners surface earlier on future suggestions.
--
-- Trigger : the existing cron `enqueue_post_publish_measurements()`
-- (worker/main.py:403, Phase E Pilier 7). We extend it to also enqueue
-- a `measure_publish_outcome` job for items whose target_url_source =
-- 'media_replacement' once `published_at < now - 14 days` AND we have at
-- least one ScanLLMResult dated post-publish.
--
-- Granularity : one row per content_item_id (UNIQUE). measured_at NULL
-- means the job is still pending. Re-runs UPDATE in place (no duplicate
-- rows; cron must check measured_at).
--
-- citation_lift_t14_per_provider shape :
--   {
--     "openai":  {"baseline_pos": 7, "latest_pos": 3, "lift": +4, "cited_now": true},
--     "gemini":  {"baseline_pos": null, "latest_pos": 5, "lift": +5, "cited_now": true},
--     "claude":  {"baseline_pos": 4, "latest_pos": 4, "lift": 0,  "cited_now": true}
--   }
-- Providers with <2 samples (one row pre and one post) are omitted.
--
-- See project_phase_mr1_media_catalog.md and
-- project_phase_e_pilier7_measurement_loop.md.

CREATE TABLE IF NOT EXISTS media_publish_outcome (
    id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_item_id                 UUID NOT NULL REFERENCES scan_content_items(id) ON DELETE CASCADE,
    domain                          TEXT NOT NULL,
    published_at                    TIMESTAMP NOT NULL,
    measured_at                     TIMESTAMP,
    citation_lift_t14_per_provider  JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                      TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT media_publish_outcome_item_uniq UNIQUE (content_item_id)
);

CREATE INDEX IF NOT EXISTS idx_media_publish_outcome_domain
    ON media_publish_outcome (domain);

CREATE INDEX IF NOT EXISTS idx_media_publish_outcome_pending
    ON media_publish_outcome (measured_at) WHERE measured_at IS NULL;

COMMENT ON TABLE media_publish_outcome IS
    'Phase MR.1 — T+14 LLM-citation lift per provider after publishing on a '
    'media suggested by /suggest-media. Closes the learning loop into '
    'media_catalog.llm_citation_decayed. Populated by Sprint 4.';
