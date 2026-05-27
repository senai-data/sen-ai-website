-- 049_scan_competitor_pages.sql
--
-- Sprint 7 (Competitor Reverse-Engineering) - feature #6 from
-- project_10_action_features.md. For each scan we surface the top
-- competitors by win-count (questions where they were cited and the
-- target brand wasn't), then audit the competitor pages the LLMs already
-- cite : Princeton GEO patterns (S5), JSON-LD schemas (S6) and Babbar
-- backlink authority (MR.1).
--
-- Why this table is distinct from scan_page_audits / scan_schema_audits :
--   - It's COMPETITOR-scoped (brand_id != focus brand) ; the page-audit
--     and schema-audit tables are user-pages-only (est_site_cible=true).
--   - We bundle both the GEO signal AND the schema info in one row so
--     the UI can render a single "competitor page card" without joining.
--   - The same URL can belong to two competitors (rare, e.g. press
--     comparison articles) - the UNIQUE constraint includes brand_id.
--
-- geo_audit JSONB shape (same as scan_page_audits.audit) :
--   { "signals": {...}, "scores": {...}, "issues": [...] }
--
-- schemas JSONB shape (same as scan_schema_audits.existing_schemas) :
--   [{ "type": "...", "valid": bool, "missing": [...], "raw": {...} }]
--
-- backlinks JSONB shape :
--   { "source": "media_catalog|babbar|none",
--     "da": int|null, "tf": int|null, "cf": int|null, "rd": bigint|null,
--     "checked_at": "iso" }

CREATE TABLE IF NOT EXISTS scan_competitor_pages (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id             UUID NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    brand_id            UUID NOT NULL REFERENCES client_brands(id) ON DELETE CASCADE,
    url                 TEXT NOT NULL,
    title               TEXT,
    fetched_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    fetch_status        INTEGER,
    fetch_error         TEXT,
    citation_count      INTEGER NOT NULL DEFAULT 0,
                                            -- how many LLM responses cited this URL
    winning_questions   JSONB NOT NULL DEFAULT '[]'::jsonb,
                                            -- [{"question": "...", "provider": "...", "scan_question_id": "..."}]
    geo_audit           JSONB NOT NULL DEFAULT '{}'::jsonb,
    geo_score           INTEGER,
    schemas             JSONB NOT NULL DEFAULT '[]'::jsonb,
    schema_score        INTEGER,
    backlinks           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE (scan_id, brand_id, url),
    CONSTRAINT cp_geo_score_range CHECK (geo_score IS NULL OR (geo_score >= 0 AND geo_score <= 100)),
    CONSTRAINT cp_schema_score_range CHECK (schema_score IS NULL OR (schema_score >= 0 AND schema_score <= 100))
);

CREATE INDEX IF NOT EXISTS idx_scp_scan        ON scan_competitor_pages(scan_id);
CREATE INDEX IF NOT EXISTS idx_scp_scan_brand  ON scan_competitor_pages(scan_id, brand_id);
CREATE INDEX IF NOT EXISTS idx_scp_scan_geo    ON scan_competitor_pages(scan_id, geo_score DESC NULLS LAST);

COMMENT ON TABLE scan_competitor_pages IS
    'Sprint 7 competitor reverse-engineering. One row per (scan, competitor brand, url) '
    'where url is a page the LLMs already cite for that competitor during the scan. '
    'Bundles Princeton GEO audit (S5), JSON-LD schemas (S6) and Babbar backlinks '
    '(MR.1) so the UI can render a "what they have that you don''t" pattern delta. '
    'See worker/handlers/audit_competitor_pages.py + project_10_action_features.md #6.';
