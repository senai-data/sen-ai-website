-- 060_byok_organization_api_keys.sql
-- BYOK beta : org-level LLM API keys (openai/anthropic/gemini/mistral).
-- Keys are Fernet-encrypted application-side with OAUTH_FERNET_KEY (same infra
-- as oauth_connections, migration 010 - rotation = re-encrypt all rows).
-- The dormant client-scoped client_api_keys table (created by create_all, zero
-- runtime usage) is intentionally left untouched - superseded by this table,
-- candidate for a later cleanup migration.
--
-- Apply: ssh root@... "docker exec -i senai-postgres psql -U senai -d senai" < api/migrations/060_byok_organization_api_keys.sql

CREATE TABLE IF NOT EXISTS organization_api_keys (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    provider            TEXT NOT NULL,            -- 'openai'|'anthropic'|'gemini'|'mistral'
    api_key_encrypted   TEXT NOT NULL,            -- Fernet ciphertext (TEXT: Fernet overhead outgrows VARCHAR(500))
    key_hint            TEXT NOT NULL DEFAULT '', -- masked display 'sk-pr...abc4' - NEVER the full key
    status              TEXT NOT NULL DEFAULT 'active',   -- 'active' | 'invalid'
    monthly_cap_usd     NUMERIC(10,2),            -- NULL = no user cap
    created_by_user_id  UUID REFERENCES users(id) ON DELETE SET NULL,
    last_validated_at   TIMESTAMP,
    last_error          TEXT,                     -- last validation/runtime auth error (actionable, key-free)
    created_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_org_api_keys_org_provider UNIQUE (organization_id, provider),
    CONSTRAINT ck_org_api_keys_provider CHECK (provider IN ('openai','anthropic','gemini','mistral')),
    CONSTRAINT ck_org_api_keys_status   CHECK (status IN ('active','invalid'))
);

CREATE INDEX IF NOT EXISTS idx_org_api_keys_org ON organization_api_keys(organization_id);

-- Spend attribution : the BYOK monthly cap must count ONLY spend made WITH the
-- org key ("don't spend more than $X/month on MY provider account"). Default
-- 'platform' is correct for every legacy row and every unmodified caller.
ALTER TABLE llm_usage_log
    ADD COLUMN IF NOT EXISTS key_source TEXT NOT NULL DEFAULT 'platform';  -- 'platform' | 'byok'

-- Partial index sized for the monthly cap SUM (byok rows are a small subset).
CREATE INDEX IF NOT EXISTS idx_llm_usage_byok_monthly
    ON llm_usage_log(client_id, provider, created_at) WHERE key_source = 'byok';

-- One-time BYOK beta bonus (200 scan credits at first complete setup) - idempotence stamp.
ALTER TABLE organizations
    ADD COLUMN IF NOT EXISTS byok_bonus_granted_at TIMESTAMP;
