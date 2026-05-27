-- 047_scan_page_audits.sql
--
-- Sprint 5 (Princeton GEO content audit) - feature #4 from
-- project_10_action_features.md. Aggarwal et al. (KDD '24) identified 7 page
-- patterns that lift LLM citation by up to 40% : Statistics Addition,
-- Cite Sources, Quotation Addition, Authoritative Phrasing, Fluency
-- Optimization, Easy-to-Understand, Unique Words. We audit each URL the
-- LLMs already cited for the user's site and emit concrete tickets.
--
-- Why this table instead of reusing client_brand_pages :
--   - client_brand_pages is sitemap-sourced and ENTITY-bound (brand). The
--     audit is SCAN-bound (different scans cite different subsets of URLs,
--     re-runs against the same brand fetch the page again).
--   - The 7-pattern signals + scores are this feature's only consumer.
--     Coupling them to the sitemap pipeline (which is run on demand and
--     often empty for new clients) would block the feature.
--
-- audit JSONB shape :
--   {
--     "signals": {
--        "statistics_count": 5,
--        "external_citations_count": 3,
--        "quotation_count": 1,
--        "authoritative_phrases_count": 2,
--        "word_count": 1247,
--        "avg_sentence_length": 18.2,
--        "flesch_score": 64,
--        "unique_terms_ratio": 0.42
--     },
--     "scores": {
--        "statistics_addition": 65,
--        "cite_sources": 50,
--        "quotation_addition": 20,
--        "authoritative_phrasing": 70,
--        "fluency": 80,
--        "easy_to_understand": 75,
--        "unique_words": 60
--     },
--     "issues": [
--        {"pattern": "statistics_addition", "severity": "medium",
--         "message": "Only 1 statistic on this page. Add 2-3 more numerical claims..."},
--        ...
--     ]
--   }

CREATE TABLE IF NOT EXISTS scan_page_audits (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id         UUID NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    url             TEXT NOT NULL,
    title           TEXT,
    lang            TEXT,
    fetched_at      TIMESTAMP NOT NULL DEFAULT NOW(),
    fetch_status    INTEGER,                  -- HTTP status, NULL on network error
    fetch_error     TEXT,                     -- short error blurb when fetch failed
    audit           JSONB NOT NULL DEFAULT '{}'::jsonb,
    geo_score       INTEGER,                  -- composite 0-100, NULL when fetch failed
    citation_count  INTEGER NOT NULL DEFAULT 0,
                                              -- how many times this URL was cited
                                              -- across the scan's LLM responses
    created_at      TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE (scan_id, url),
    CONSTRAINT geo_score_range CHECK (geo_score IS NULL OR (geo_score >= 0 AND geo_score <= 100))
);

CREATE INDEX IF NOT EXISTS idx_spa_scan ON scan_page_audits(scan_id);
CREATE INDEX IF NOT EXISTS idx_spa_scan_geo ON scan_page_audits(scan_id, geo_score DESC NULLS LAST);

COMMENT ON TABLE scan_page_audits IS
    'Sprint 5 GEO content audit. One row per (scan, url) where url is a page of '
    'the user''s own site that was cited by at least one LLM during the scan. '
    'The 7-pattern signals come from the Princeton GEO paper (Aggarwal KDD 24). '
    'See worker/handlers/audit_scan_pages.py + project_10_action_features.md #4.';
