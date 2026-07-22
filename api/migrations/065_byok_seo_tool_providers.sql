-- 065_byok_seo_tool_providers.sql
-- BYOK extension : allow org-level keys for the external SEO tools used by the
-- content pipeline (YourTextGuru + Babbar). HaloScan is whitelisted here too
-- so a later Phase 3 runtime wiring needs no second migration, but it is NOT
-- exposed for entry yet (llm_key_validator.BYOK_SEO_PROVIDERS = ytg + babbar).
--
-- These are NOT LLM keys : they carry no llm_usage_log $ spend, so the
-- monthly_cap_usd machinery does not apply to them (leave NULL). Runtime
-- resolution reuses worker/services/byok.resolve_org_key via new
-- resolve_ytg_key / resolve_babbar_key wrappers; injection is a per-job env
-- patch in generate_article (YOURTEXTGURU_API_KEY / BABBAR_API_KEY).
--
-- Only the provider CHECK constraint changes; storage, encryption, uniqueness
-- (organization_id, provider) and status semantics are unchanged from 060.
--
-- Apply: ssh root@... "docker exec -i senai-postgres psql -U senai -d senai" < api/migrations/065_byok_seo_tool_providers.sql

ALTER TABLE organization_api_keys
    DROP CONSTRAINT IF EXISTS ck_org_api_keys_provider;

ALTER TABLE organization_api_keys
    ADD CONSTRAINT ck_org_api_keys_provider
    CHECK (provider IN ('openai','anthropic','gemini','mistral','yourtextguru','babbar','haloscan'));
