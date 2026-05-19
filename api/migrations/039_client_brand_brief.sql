-- 039_client_brand_brief.sql
--
-- Phase BB — Per-brand brief : surcharge the workspace client.brief on a
-- per-primary-brand basis so generators can use brand-specific voice,
-- audience, competitors, etc.
--
-- Storage : one new JSONB column on client_brands. No new table — 1 row per
-- ClientBrand already = 1 brief slot. Validation is enforced at the worker
-- boundary via worker/schemas.py:BrandBrief.
--
-- Backward compat : NULL means "not generated yet" — downstream readers fall
-- back to the workspace client.brief via worker/adapters/brief_injector.py's
-- 2-level merge (brand wins per-field, workspace fills gaps).
--
-- See project_phase_brand_briefs.md (BB.7).

ALTER TABLE client_brands
    ADD COLUMN IF NOT EXISTS brief                    JSONB,
    ADD COLUMN IF NOT EXISTS brief_generated_at       TIMESTAMP,
    ADD COLUMN IF NOT EXISTS brief_generations_count  INTEGER NOT NULL DEFAULT 0;

COMMENT ON COLUMN client_brands.brief IS
    'BrandBrief JSONB — surcharges client.brief workspace per-field for voice, '
    'audience, competitors, regulatory, etc. Validated by worker/schemas.py:BrandBrief. '
    'NULL = not generated yet, downstream readers fall back to workspace brief. '
    'The dict carries an edited_by_user bool (set by manual PATCH) which blocks '
    'regen — mirrors the same flag on client.apps.client_brief. See migration 039.';

COMMENT ON COLUMN client_brands.brief_generated_at IS
    'Timestamp of the most recent successful generate_brand_brief run. '
    'NULL when never generated or only manually edited.';

COMMENT ON COLUMN client_brands.brief_generations_count IS
    'How many times generate_brand_brief has run on this row. Capped at '
    'MAX_BRAND_BRIEF_GENERATIONS (=3) to bound LLM spend per brand. '
    'See worker/handlers/generate_brand_brief.py.';
