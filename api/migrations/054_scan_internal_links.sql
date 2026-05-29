-- 054_scan_internal_links.sql
--
-- Sprint 11 (Internal Linking Audit) - feature #9 from
-- project_10_action_features.md. Audits the internal link graph among the
-- pages of the user's own site that LLMs cite during this scan. Surfaces
-- topology issues (orphans, hubs, dead-ends), anchor quality issues
-- (generic "click here" anchors, empty alt-text image links, duplicated
-- anchors pointing to different URLs), and a per-page linking_score.
--
-- Source of URLs : same set the Page Audit (S5) uses - the user's own
-- pages cited by LLMs in this scan. We re-fetch each so the link graph
-- is current ; this is wasteful but isolated to its own row set (no
-- coupling with scan_page_audits).
--
-- outbound_internal_links JSONB shape :
--   [{ "target": "https://example.com/page-b",
--      "anchor": "Discover the routine",
--      "anchor_lower": "discover the routine",   -- precomputed for fast dedupe
--      "is_generic": false,
--      "is_empty": false,
--      "is_image": false,
--      "rel": "noopener",                         -- "" when absent
--      "position": "main"                          -- "main" | "nav" | "footer" | null
--    }, ...]
--
-- issues JSONB shape :
--   [{ "type": "generic_anchor",
--      "severity": "medium",
--      "anchor": "cliquez ici",
--      "target": "https://example.com/produit-x",
--      "message": "Anchor text 'cliquez ici' carries no semantic signal..."
--    }, ...]
--
-- linking_score (0-100) composite :
--   40 pts anchor quality   (1 - generic_count / outbound_count) × 40
--   30 pts diversity        avg_anchor_length × number_of_unique_targets
--   15 pts dead-end penalty linkless pages cap the score at 50
--   15 pts depth            number of outbound internal links (saturates at 8)

CREATE TABLE IF NOT EXISTS scan_internal_links (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id                  UUID NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    url                      TEXT NOT NULL,
    title                    TEXT,
    fetched_at               TIMESTAMP NOT NULL DEFAULT NOW(),
    fetch_status             INTEGER,
    fetch_error              TEXT,
    outbound_internal_count  INTEGER NOT NULL DEFAULT 0,
    outbound_external_count  INTEGER NOT NULL DEFAULT 0,
    generic_anchor_count     INTEGER NOT NULL DEFAULT 0,
    empty_anchor_count       INTEGER NOT NULL DEFAULT 0,
    duplicate_anchor_count   INTEGER NOT NULL DEFAULT 0,
    avg_anchor_length        REAL,
    outbound_internal_links  JSONB NOT NULL DEFAULT '[]'::jsonb,
    issues                   JSONB NOT NULL DEFAULT '[]'::jsonb,
    linking_score            INTEGER,
    citation_count           INTEGER NOT NULL DEFAULT 0,
    created_at               TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE (scan_id, url),
    CONSTRAINT sil_linking_score_range CHECK (
      linking_score IS NULL OR (linking_score >= 0 AND linking_score <= 100)
    )
);

CREATE INDEX IF NOT EXISTS idx_sil_scan          ON scan_internal_links(scan_id);
CREATE INDEX IF NOT EXISTS idx_sil_scan_score    ON scan_internal_links(scan_id, linking_score DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_sil_outbound_gin  ON scan_internal_links USING GIN (outbound_internal_links);

COMMENT ON TABLE scan_internal_links IS
    'Sprint 11 internal linking audit. One row per (scan, url) where url is '
    'a page of the user''s own site cited by an LLM in this scan. Stores the '
    'parsed outbound internal-link graph + per-page anchor quality issues. '
    'Topology stats (orphans / hubs / dead-ends) are computed at read time '
    'from the outbound_internal_links arrays. See worker/handlers/'
    'audit_internal_links.py + project_10_action_features.md #9.';

COMMENT ON COLUMN scan_internal_links.outbound_internal_links IS
    'JSONB array of {target, anchor, anchor_lower, is_generic, is_empty, '
    'is_image, rel, position}. One entry per <a href> pointing to a URL '
    'on the same primary host as the source page. External links are '
    'counted (outbound_external_count) but not stored.';

COMMENT ON COLUMN scan_internal_links.linking_score IS
    'Composite 0-100. 40 pts anchor quality (low generic ratio), 30 pts '
    'diversity (avg anchor length × unique targets), 15 pts dead-end '
    'penalty (linkless pages cap at 50), 15 pts depth (outbound count).';
