-- 043_media_catalog_babbar_split.sql
--
-- Phase MR.1.5 — Split authority enrichment from price enrichment.
--
-- After the first prod run of discover_media_catalog (2026-05-21) we found
-- LinkFinder returns `prix_ht` reliably but `da/tf/cf/rd` come back NULL
-- across the board (53 priced rows, 0 with DA). LinkFinder's authority
-- metrics are tied to a tier we don't have / are sourced from third parties
-- they don't always carry.
--
-- Babbar.tech (`worker/seo_llm/src/babbar_client.py`) already exists in the
-- submodule and reliably returns hostTrust (=BAS), domainTrust, semanticValue,
-- backlinksCount via /host/overview/main. We swap data sources :
--   - da/tf/cf/rd  ← Babbar enricher (new)
--   - price_eur    ← LinkFinder (unchanged, its only real value-add)
--
-- The two enrichers have different cadences (Babbar metrics shift slower
-- than netlinking prices) so we track their last-check timestamps
-- independently. This migration adds `babbar_last_check` and re-purposes
-- `linkfinder_last_check` to mean "price-source last check" only.
--
-- Additive only.

ALTER TABLE media_catalog
    ADD COLUMN IF NOT EXISTS babbar_last_check TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_media_catalog_babbar_check_null
    ON media_catalog (babbar_last_check)
 WHERE babbar_last_check IS NULL;

COMMENT ON COLUMN media_catalog.linkfinder_last_check IS
    'Last LinkFinder.get_prices_batch call. NULL = never checked. '
    'Marks ONLY the price-source attempt; authority columns (da/tf/cf/rd) '
    'are tracked separately via babbar_last_check since the two enrichers '
    'have different cadences (Babbar ~30d, LinkFinder ~7d).';

COMMENT ON COLUMN media_catalog.babbar_last_check IS
    'Last Babbar /host/overview/main call. NULL = never checked. '
    'Marks the authority-source attempt that fills da (=hostTrust/BAS), '
    'tf (=domainTrust), cf (=semanticValue), rd (=backlinksCount). '
    'See worker/services/media_catalog_io.py:enrich_with_babbar.';

COMMENT ON COLUMN media_catalog.da IS
    'Domain authority score 0-100. Sourced from Babbar hostTrust '
    '(=babbarAuthorityScore / BAS). Was Moz DA via LinkFinder before MR.1.5.';

COMMENT ON COLUMN media_catalog.tf IS
    'Trust score 0-100. Sourced from Babbar domainTrust. '
    'Was Majestic Trust Flow via LinkFinder before MR.1.5.';

COMMENT ON COLUMN media_catalog.cf IS
    'Semantic value 0-100. Sourced from Babbar semanticValue. '
    'Was Majestic Citation Flow via LinkFinder before MR.1.5.';

COMMENT ON COLUMN media_catalog.rd IS
    'Backlinks count. Sourced from Babbar backlinks.linkCount. '
    'Was Referring Domains (Ahrefs/Majestic via LinkFinder) before MR.1.5.';
