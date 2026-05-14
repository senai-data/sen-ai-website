-- 028_client_brand_sitemap_config.sql
--
-- Phase D — Multi-locale brand support + manual sitemap-URL override.
--
-- Many international brands serve content in 5-20+ locales (cf. Ducray :
-- /fr-fr/, /es-es/, /de-de/, /it-it/, /es-mx/, /pt-pt/, /fr-be/, ...). Their
-- /sitemap.xml at the root is a sitemapindex that recurses into every
-- locale's sub-sitemap. Without filtering, the crawler ingests all of
-- them — which (a) blows the 5000-URL cap on multi-product brands, and
-- (b) leaves the embedding corpus mixing French and Spanish FAQ-relevant
-- text. The matcher would then surface the wrong-language page for
-- target_url suggest.
--
-- Two complementary fields to fix this cleanly :
--
-- - `sitemap_urls_override` : JSONB array of canonical sitemap URLs. When
--   non-empty, the crawler SKIPS discovery entirely (no robots.txt read,
--   no /sitemap.xml fallback) and crawls exactly those URLs. Validation
--   at the API layer keeps them on the brand's own domain. The escape
--   hatch for sites with non-standard sitemap locations or for brands
--   that need a hand-curated list (e.g. a corporate site where /sitemap
--   includes irrelevant news/event sitemaps).
--
-- - `locale_path_prefix` : a TEXT path fragment like '/fr-fr/'. Used as
--   both (a) a sub-sitemap SKIP filter during sitemapindex recursion
--   (don't bother fetching a sub-sitemap whose URL doesn't include this
--   prefix), and (b) a defensive post-filter on the final URL list
--   (catches the case where one big sitemap contains all locales).
--   Empty/NULL = no filter, current behavior.
--
-- The two fields compose : `sitemap_urls_override` wins on discovery,
-- `locale_path_prefix` always applies as a post-filter.
--
-- v2 may add multi-prefix support (e.g. ['/fr-fr/', '/fr-be/'] to crawl
-- two locales of a brand). For now single-prefix keeps the Settings UI
-- simple — multi-locale brands generally pick one canonical locale to
-- target per workspace.

ALTER TABLE client_brands
    ADD COLUMN IF NOT EXISTS sitemap_urls_override JSONB
    NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE client_brands
    ADD COLUMN IF NOT EXISTS locale_path_prefix TEXT;

COMMENT ON COLUMN client_brands.sitemap_urls_override IS
    'JSONB array of canonical sitemap URLs that override auto-discovery. '
    'When non-empty, crawler crawls exactly these URLs and skips '
    'robots.txt + /sitemap.xml discovery. See migration 028.';

COMMENT ON COLUMN client_brands.locale_path_prefix IS
    'Path fragment (e.g. /fr-fr/) used as a sub-sitemap skip filter '
    'AND a final URL post-filter. NULL = no locale restriction. '
    'See migration 028.';
