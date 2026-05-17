-- 032_client_brand_vertical_metadata.sql
--
-- Phase C.1 — Multi-vertical injection layer for the netlinking article
-- generator (`worker/handlers/generate_article.py`).
--
-- The seo_llm submodule (`worker/seo_llm/src/geo_content_generator.py`)
-- hardcodes Pierre-Fabre-specific data in several module-level constants :
--   - BRAND_EXPERT_SECTIONS  : brand_site → list of path fragments where
--                              expert content lives (e.g. /votre-peau)
--   - GAMME_TO_SITE          : product_line → brand_site mapping (inverse
--                              of "which gammes does this brand sell ?")
--
-- The SaaS wrapper SHADOWS those constants at runtime via per-client data
-- pulled from `client_brands`. To make the wrapper truly vertical-agnostic
-- we need those two fields per ClientBrand row. Empty/NULL = the article
-- pipeline runs in graceful-degradation mode (no expert-section scraping
-- and no gamme→site post-processing for that brand). For Pierre Fabre, we
-- will seed both fields from target_brands_config.json in a follow-up
-- one-shot script ; for any future client, the Settings UI will let the
-- user fill them — or leave them empty and accept the minor degradation.
--
-- Both columns default to '[]'::jsonb so existing rows keep working
-- unchanged. The wrapper code treats empty lists as "no specific signals,
-- fall back to generic scraping / generic prompt" — same as if the
-- constant lookup had missed in seo_llm.

ALTER TABLE client_brands
    ADD COLUMN IF NOT EXISTS expert_section_paths JSONB
    NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE client_brands
    ADD COLUMN IF NOT EXISTS product_lines JSONB
    NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN client_brands.expert_section_paths IS
    'List of path fragments on this brand''s site where expert / advice / '
    'editorial content lives (e.g. ["/votre-peau", "/conseils-d-experts"]). '
    'Used by worker/handlers/generate_article.py to scrape expert pages '
    'when building the brand_content grounding block. Empty = generic home '
    'scraping. Replaces the seo_llm BRAND_EXPERT_SECTIONS hardcode for '
    'multi-vertical support. See migration 032.';

COMMENT ON COLUMN client_brands.product_lines IS
    'List of product line / gamme names this brand owns '
    '(e.g. ["XeraCalm", "Cleanance", "Hydrance"]). Used by '
    'worker/handlers/generate_article.py to build the inverse '
    '"gamme→brand_site" mapping that seo_llm uses for tableau '
    'wrapper post-processing. Empty = no per-gamme link injection. '
    'Replaces the seo_llm GAMME_TO_SITE hardcode for multi-vertical '
    'support. See migration 032.';
