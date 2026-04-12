-- Migration 010: OAuth delegation infrastructure (Phase 0).
--
-- Each row represents an external account that a sen-ai.fr client has
-- delegated to us via OAuth. The plaintext access/refresh tokens are
-- never stored — they're encrypted application-side with Fernet using
-- the OAUTH_FERNET_KEY env var (rotation = re-encrypt all rows).
--
-- One client can have many connections of the same product type
-- (e.g. Pierre Fabre has 6 Google Ads MCC accounts → 6 rows with
--  provider='google', product='google_ads', different account_id).
-- The partial UNIQUE index keeps duplicate-active rows out: revoking
-- a connection flips status='revoked' (or hard-deletes), which frees
-- the slot for a fresh re-connect of the same account.
--
-- Cascade: ON DELETE CASCADE from clients matches the GDPR cascade
-- chain installed in migration 008 — deleting a client wipes its
-- OAuth connections too. authorized_by_user_id is SET NULL so the
-- audit field survives a user deletion.

CREATE TABLE IF NOT EXISTS oauth_connections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,

  provider VARCHAR(50) NOT NULL,        -- 'google', 'microsoft', 'notion'
  product VARCHAR(50) NOT NULL,         -- 'google_ads', 'ga4', 'gbp', 'sheets', 'drive', 'sharepoint', 'notion'

  -- Identity of the external account that granted access
  account_id VARCHAR(255),              -- provider-specific id (Google sub, MS oid, Notion workspace_id)
  account_email VARCHAR(255),
  account_name VARCHAR(255),

  -- Encrypted tokens (Fernet ciphertext, base64 ASCII)
  access_token_encrypted TEXT,
  refresh_token_encrypted TEXT,
  token_expires_at TIMESTAMP,           -- NULL if provider doesn't expose expiry
  scopes TEXT[],                        -- granted scopes after consent

  -- Provider-specific config (e.g. {ads_customer_id: '123', mcc_id: '456'})
  config JSONB NOT NULL DEFAULT '{}',

  -- Lifecycle
  status VARCHAR(20) NOT NULL DEFAULT 'active',  -- 'active' | 'expired' | 'revoked'
  authorized_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  authorized_at TIMESTAMP NOT NULL DEFAULT NOW(),
  last_used_at TIMESTAMP,
  revoked_at TIMESTAMP,

  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Lookup pattern: "give me the active connection of type X for client Y"
CREATE INDEX IF NOT EXISTS idx_oauth_connections_client_product_status
  ON oauth_connections (client_id, product, status);

-- One active connection per (client, provider, product, account_id).
-- Allows multi-account setups (Pierre Fabre 6 MCCs) but blocks accidental
-- duplicates from a re-clicked Connect button. account_id is COALESCE-d to
-- empty string in the index expression so accounts that haven't yet been
-- userinfo-resolved don't all collide on NULL.
CREATE UNIQUE INDEX IF NOT EXISTS idx_oauth_connections_unique_active
  ON oauth_connections (client_id, provider, product, COALESCE(account_id, ''))
  WHERE status = 'active';
