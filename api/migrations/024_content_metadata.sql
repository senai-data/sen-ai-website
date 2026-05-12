-- 024_content_metadata.sql
--
-- Audit metadata persisted alongside generated content. Captures the
-- quality_score + sources cited at the time of generation so the validation
-- UI can show "91/100, 3 sources cited (0 on competitor sites)" without
-- joining back to the job log (which can disappear, and which couples the
-- UI to worker internals).
--
-- Written by worker/handlers/generate_faq.py on every successful generation.
-- Schema (all optional — old rows have empty {}) :
--   {
--     "quality_score": 91,
--     "faq_count": 5,
--     "sources_used": [
--       {"url": "...", "org": "Avène", "type": "brand_site"},
--       {"url": "...", "org": "Société Française de Dermatologie", "type": "society"},
--       ...
--     ],
--     "competitor_drops": 0,           -- diagnostic: URLs dropped by denylist
--     "drop_reasons": {"competitor": 0, "ecommerce_path": 0, "social": 0},
--     "generated_at": "2026-05-12T21:43:23",
--     "duration_ms": 28824,
--     "generator_version": "denylist-prefer-hint-v1"
--   }
--
-- Future content types (Phase C article gen) reuse this column with their
-- own schema variants — keep additive, never break existing keys.

ALTER TABLE scan_content_items
    ADD COLUMN IF NOT EXISTS content_metadata JSONB
    NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN scan_content_items.content_metadata IS
    'Audit metadata for the generated content: quality_score, sources cited '
    'with org names, denylist drop counts, generation timestamp + duration. '
    'Written by worker on every successful generate_faq / generate_article run.';
