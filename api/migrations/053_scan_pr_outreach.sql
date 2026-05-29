-- 053_scan_pr_outreach.sql
--
-- Sprint 9 (PR / Journalist Outreach List) - feature #7 from
-- project_10_action_features.md. For each scan we surface the press / media
-- domains that LLMs cite for competitors but NOT for the focus brand, so the
-- user gets a shortlist of journalists / publications to engage with.
--
-- Source of URLs : scan_llm_results.citations[] for this scan, filtered to
-- domains that look like press/media. We classify a domain as "media" if it
-- exists in media_catalog with site_type='media' OR if it doesn't match the
-- focus brand domain, any competitor brand domain, or known platform domains
-- (reddit/wikipedia/youtube). Same architectural choice as Sprint 7/8 -
-- mine what wins right now, no SERP / external discovery in v1.
--
-- One row per (scan, domain). Per-domain we aggregate :
--   - competitor_brands : sorted list of competitor brand names cited at
--                         this domain in this scan
--   - target_cited      : true if the focus brand was also cited at this
--                         domain (= lost ground rather than opportunity)
--   - citation_count    : total LLM citations pointing at this domain
--   - top_pages JSONB   : up to 5 URLs with title/contexte/winning_questions
--                         so the UI can show "what they wrote about" without
--                         a follow-up DB call
--   - authority signals : Babbar DA/TF/CF/RD copied from media_catalog when
--                         present (the catalog row stays the source of truth)
--   - in_catalog        : true if media_catalog has the domain (so the UI
--                         can render the price/vertical chips). NULL outside
--                         the catalog means "discovered through citations,
--                         not yet enriched."
--   - leverage_score    : 0-100 composite (citation_count × competitor_count
--                         × authority lever × novelty lever where novelty =
--                         not already cited for the focus brand).
--
-- classification :
--   competitor_only : ≥1 competitor cited, target not cited      = opportunity
--   shared          : ≥1 competitor cited AND target cited       = lost ground
--   target_only     : only target cited (informational, low prio = positive
--                     footprint, not surfaced by default)
--
-- The UI filters on classification='competitor_only' by default.

CREATE TABLE IF NOT EXISTS scan_pr_outreach (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id                  UUID NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    domain                   TEXT NOT NULL,
    site_type                TEXT,
    citation_count           INTEGER NOT NULL DEFAULT 0,
    competitor_brands        TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    target_cited             BOOLEAN NOT NULL DEFAULT false,
    classification           TEXT,
    top_pages                JSONB NOT NULL DEFAULT '[]'::jsonb,
    winning_questions        JSONB NOT NULL DEFAULT '[]'::jsonb,
    da                       INTEGER,
    tf                       INTEGER,
    cf                       INTEGER,
    rd                       BIGINT,
    price_eur                NUMERIC(10,2),
    vertical                 TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    audience_tags            TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    editorial_voice          TEXT,
    in_catalog               BOOLEAN NOT NULL DEFAULT false,
    leverage_score           INTEGER,
    created_at               TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE (scan_id, domain),
    CONSTRAINT pr_classification_values CHECK (
      classification IS NULL OR classification IN ('competitor_only', 'shared', 'target_only')
    ),
    CONSTRAINT pr_leverage_score_range CHECK (
      leverage_score IS NULL OR (leverage_score >= 0 AND leverage_score <= 100)
    )
);

CREATE INDEX IF NOT EXISTS idx_spr_scan          ON scan_pr_outreach(scan_id);
CREATE INDEX IF NOT EXISTS idx_spr_scan_leverage ON scan_pr_outreach(scan_id, leverage_score DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_spr_scan_class    ON scan_pr_outreach(scan_id, classification);

COMMENT ON TABLE scan_pr_outreach IS
    'Sprint 9 PR / journalist outreach list. One row per (scan, media domain) '
    'where the domain was cited by at least one LLM during this scan for a '
    'competitor or the focus brand. Authority signals copied from '
    'media_catalog when the domain is enriched ; otherwise null. '
    'See worker/handlers/build_pr_outreach.py + project_10_action_features.md #7.';

COMMENT ON COLUMN scan_pr_outreach.top_pages IS
    'JSONB array of up to 5 {url, contexte, title, citation_count, '
    'winning_questions[], competitor_brands[], target_cited} so the UI '
    'can render the "what they wrote" drill-down without a follow-up call.';

COMMENT ON COLUMN scan_pr_outreach.leverage_score IS
    'Composite 0-100. 40 pts citation_count engagement, 30 pts competitor '
    'breadth (more competitors at one domain = stronger pattern), 20 pts '
    'authority lever (Babbar DA when present, 0 otherwise), 10 pts novelty '
    '(target NOT also cited = pure opportunity).';
