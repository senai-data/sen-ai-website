-- 040_media_catalog.sql
--
-- Phase MR.1 — Suggest Alternative Media (Sprint 1).
--
-- A vertical-agnostic catalog of buyable media used to suggest replacement
-- target_url for netlinking_article content items when the in-scan media_picker
-- yields zero candidates (NEEDS_MEDIA_URL) or a price=null candidate.
--
-- Source of truth: bootstrapped from `scan_llm_results.citations` aggregation
-- (handler `worker/handlers/discover_media_catalog.py`) then enriched per-domain
-- via LinkFinder.get_prices_batch (DA/TF/CF/RD/price_eur). LinkFinder is a pure
-- enricher here — it is NOT the catalog source (it has no category API).
--
-- Multi-tenant invariant : PK is (domain, country, language) so `marieclaire.fr`
-- can co-exist as a French-only row without polluting an Spanish-language scan.
-- See feedback_no_hardcoded_vertical.md — no vertical-specific allowlist lives
-- in code; everything ranks via `vertical TEXT[]` populated from
-- `client.vertical` of the citing scans.
--
-- Decay : `llm_citation_decayed` is the citation_count weighted by
-- 0.9^months_old so a 2-year-old citation contributes ~0.1× a fresh one.
-- Recomputed nightly by the same handler. `llm_citation_count` keeps the
-- raw lifetime tally for debugging.
--
-- See project_phase_mr1_media_catalog.md memory.

CREATE TABLE IF NOT EXISTS media_catalog (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    domain                   TEXT NOT NULL,
    country                  TEXT NOT NULL,
    language                 TEXT NOT NULL,
    vertical                 TEXT[] NOT NULL DEFAULT '{}',
    topic_areas              TEXT[] NOT NULL DEFAULT '{}',
    editorial_voice          TEXT,
    audience_tags            TEXT[] NOT NULL DEFAULT '{}',
    media_group              TEXT,
    price_eur                NUMERIC(10,2),
    da                       INTEGER,
    tf                       INTEGER,
    cf                       INTEGER,
    rd                       INTEGER,
    llm_citation_count       INTEGER NOT NULL DEFAULT 0,
    llm_citation_decayed     NUMERIC(10,3) NOT NULL DEFAULT 0,
    llm_citation_last_seen   TIMESTAMP,
    reputation_flags         TEXT[] NOT NULL DEFAULT '{}',
    site_type                TEXT,
    linkfinder_last_check    TIMESTAMP,
    created_at               TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT media_catalog_domain_locale_uniq UNIQUE (domain, country, language)
);

CREATE INDEX IF NOT EXISTS idx_media_catalog_country_language
    ON media_catalog (country, language);

CREATE INDEX IF NOT EXISTS idx_media_catalog_vertical
    ON media_catalog USING GIN (vertical);

CREATE INDEX IF NOT EXISTS idx_media_catalog_topic_areas
    ON media_catalog USING GIN (topic_areas);

CREATE INDEX IF NOT EXISTS idx_media_catalog_price
    ON media_catalog (price_eur) WHERE price_eur IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_media_catalog_decayed
    ON media_catalog (llm_citation_decayed DESC);

COMMENT ON TABLE media_catalog IS
    'Phase MR.1 — Buyable-media catalog for suggest-media endpoint. '
    'PK (domain, country, language). Bootstrapped from scan_llm_results.citations '
    'and enriched by LinkFinder. Multi-vertical via vertical[] (no hardcoded lists). '
    'See worker/handlers/discover_media_catalog.py and project_phase_mr1_media_catalog.md.';

COMMENT ON COLUMN media_catalog.llm_citation_decayed IS
    'Σ(0.9^months_old) over all citations of this (domain, country, language). '
    'Recomputed nightly. Use this for ranking, not llm_citation_count.';

COMMENT ON COLUMN media_catalog.reputation_flags IS
    'e.g. {''thin_content'', ''spam_history''}. Empty array = clean. Penalizes scoring.';

COMMENT ON COLUMN media_catalog.linkfinder_last_check IS
    'Last LinkFinder.get_prices_batch call. NULL = never checked. Throttle re-checks '
    'to >= 7 days in handler to respect the 300-domains/batch endpoint.';
