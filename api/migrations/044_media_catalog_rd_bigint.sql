-- 044_media_catalog_rd_bigint.sql
--
-- Phase MR.1.6 — Fix : rd (backlinks count) overflows INTEGER.
--
-- Babbar returns total backlink counts that exceed int4 range for large
-- sites (e.g. youtube.com = 3,450,929,230 > 2,147,483,647). The nightly
-- discover_media_catalog cron crashed on the first such row, rolling back
-- the WHOLE enrichment batch — which is why authority coverage was stuck at
-- the 5 smoke-test domains. Widen to BIGINT.
--
-- da / tf / cf stay INTEGER (0-100 scores, never overflow).

ALTER TABLE media_catalog
    ALTER COLUMN rd TYPE BIGINT;

COMMENT ON COLUMN media_catalog.rd IS
    'Backlinks count from Babbar (backlinks.linkCount). BIGINT — large sites '
    'exceed int4 range (youtube.com ~3.4B). See migration 044.';
