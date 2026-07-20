-- 062_scan_placements.sql
--
-- Placements module - track published third-party articles as cited sources.
-- Plan: C:/Users/leed/.claude/plans/placements-module.md
-- Memory: project_placements_module_diagnostic
--
-- A placement is a URL the user published on an external media (press article,
-- guest post, netlinking piece). Placements attach to the ROOT scan of a
-- lineage (scan.parent_scan_id or scan.id) so the watchlist survives rescans.
-- At every rescan completion (and on demand) the match_placements worker job
-- compares each placement URL against scan_llm_results.citations[] and writes:
--   - placement_hits       : detail rows, ONLY exact/variant/prefix matches
--                            (low volume, high value). The domain tier is
--                            NEVER stored here - a heavily-cited domain such
--                            as ameli.fr would produce hundreds of rows per
--                            rescan. Domain-level signal lives aggregated in
--                            placement_scan_stats.domain_citation_count.
--   - placement_scan_stats : one row per (placement, rescan, provider) - the
--                            "cited in X of N runs" timeline the UI reads.
--                            Rebuilt idempotently (DELETE + INSERT scoped to
--                            the rescan) on every matcher run.
--   - url_redirect_cache   : cross-tenant resolution cache for LLM grounding
--                            redirect URLs (vertexaisearch.cloud.google.com).
--                            Resolution reads the Location header only and
--                            never fetches the target (no SSRF surface).
--
-- v1 is strictly additive and READ-ONLY on existing tables: citations JSONB
-- is never modified, is_pr_source is never stamped (decision A1 in the plan).

CREATE TABLE IF NOT EXISTS scan_placements (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id             UUID NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    url                 TEXT NOT NULL,
    url_canonical       TEXT NOT NULL,
    url_path_key        TEXT NOT NULL,
    domain              TEXT NOT NULL,
    title               TEXT,
    media_name          TEXT,
    published_at        DATE,
    target_question_ids UUID[] NOT NULL DEFAULT ARRAY[]::uuid[],
    content_item_id     UUID REFERENCES scan_content_items(id) ON DELETE SET NULL,
    source              TEXT NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('manual', 'import', 'content_item')),
    http_status         INT,
    http_checked_at     TIMESTAMP,
    notes               TEXT,
    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE (scan_id, url_canonical)
);

CREATE INDEX IF NOT EXISTS idx_placements_scan   ON scan_placements(scan_id);
CREATE INDEX IF NOT EXISTS idx_placements_domain ON scan_placements(domain);

CREATE TABLE IF NOT EXISTS placement_hits (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    placement_id           UUID NOT NULL REFERENCES scan_placements(id) ON DELETE CASCADE,
    slr_id                 UUID NOT NULL REFERENCES scan_llm_results(id) ON DELETE CASCADE,
    scan_id                UUID NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    question_id            UUID,
    provider               VARCHAR(30) NOT NULL,
    run_index              INT NOT NULL,
    match_level            TEXT NOT NULL CHECK (match_level IN ('exact', 'variant', 'prefix')),
    matched_url            TEXT NOT NULL,
    resolved_from_redirect BOOLEAN NOT NULL DEFAULT false,
    citation_position      INT,
    result_created_at      TIMESTAMP NOT NULL,
    created_at             TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE (placement_id, slr_id)
);

CREATE INDEX IF NOT EXISTS idx_phits_placement ON placement_hits(placement_id, result_created_at);
CREATE INDEX IF NOT EXISTS idx_phits_scan      ON placement_hits(scan_id);

CREATE TABLE IF NOT EXISTS placement_scan_stats (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    placement_id          UUID NOT NULL REFERENCES scan_placements(id) ON DELETE CASCADE,
    scan_id               UUID NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    provider              VARCHAR(30) NOT NULL,
    runs_total            INT NOT NULL DEFAULT 0,
    runs_with_hit         INT NOT NULL DEFAULT 0,
    domain_citation_count INT NOT NULL DEFAULT 0,
    unresolved_redirects  INT NOT NULL DEFAULT 0,
    best_position         INT,
    matched_questions     JSONB NOT NULL DEFAULT '[]'::jsonb,
    scan_created_at       TIMESTAMP NOT NULL,
    computed_at           TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE (placement_id, scan_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_pstats_placement ON placement_scan_stats(placement_id, scan_created_at);

CREATE TABLE IF NOT EXISTS url_redirect_cache (
    url_hash        TEXT PRIMARY KEY,
    source_url      TEXT NOT NULL,
    resolved_url    TEXT,
    status          TEXT NOT NULL DEFAULT 'failed' CHECK (status IN ('resolved', 'failed')),
    attempts        INT NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMP
);

COMMENT ON TABLE scan_placements IS
    'Placements module - published third-party article URLs tracked per brand '
    'tracker (lineage root scan). url_canonical/url_path_key are precomputed '
    'by services/url_matching.py (PARITY api+worker). '
    'See plan placements-module.md.';

COMMENT ON TABLE placement_hits IS
    'Detail rows for exact/variant/prefix citation matches only. Domain-tier '
    'matches are aggregated in placement_scan_stats, never stored here.';

COMMENT ON COLUMN placement_scan_stats.runs_with_hit IS
    'Runs (run_index >= 1) of this provider on this rescan where at least one '
    'citation matched exact or variant. UI renders "cited in X of N runs" - '
    'never a boolean (N-runs sampling variance).';

COMMENT ON TABLE url_redirect_cache IS
    'Cross-tenant cache of LLM grounding redirect resolutions (Location header '
    'only, single hop, host allowlist). max 3 attempts lifetime.';
