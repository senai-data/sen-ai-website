-- 046_client_brand_wikipedia.sql
--
-- Sprint 4 (Wikipedia Entity Action) - feature #1 from project_10_action_features.md.
--
-- Why: ChatGPT cites Wikipedia 48% of the time (Stackmatix 30M citations study,
-- May 2026). If a brand has no Wikipedia page, it's structurally invisible in
-- the most cited source on the planet. This column caches the Wikipedia
-- presence check per brand so the UI can surface it without hitting the
-- Wikipedia REST API on every page load.
--
-- Shape of the JSONB :
--   {
--     "checked_at": "2026-05-27T11:00:00Z",  -- last check timestamp (TTL 7 days)
--     "by_lang": {
--       "fr": {
--         "exists": true,
--         "url": "https://fr.wikipedia.org/wiki/Ducray",
--         "title": "Ducray",
--         "extract": "Laboratoire dermatologique...",
--         "last_modified": "2026-04-12T09:23:00Z",
--         "references_count": 14,
--         "categories_count": 3,
--         "page_views_30d": 4521,
--         "quality_score": 72  -- 0-100 composite, see services/wikipedia_score.py
--       },
--       "en": { ... }
--     }
--   }
--
-- Default '{}' = not checked yet. The worker handler check_brand_wikipedia.py
-- populates this for the focus brand + classified competitors of each scan.
-- See worker/handlers/check_brand_wikipedia.py + plan lovely-skipping-sunset.md.

ALTER TABLE client_brands
    ADD COLUMN wikipedia JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN client_brands.wikipedia IS
    'Wikipedia presence cache. Per-language data (FR, EN, ...) populated by '
    'worker/handlers/check_brand_wikipedia.py. TTL ~7 days. Cf. migration 046 '
    '+ project_10_action_features.md feature #1.';
