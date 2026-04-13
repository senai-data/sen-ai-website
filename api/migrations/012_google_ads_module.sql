-- Migration 012: Google Ads Intelligence module (Phase 1).
--
-- Extends the jobs table for non-scan jobs (sync operations), and creates
-- the core tables for Google Ads campaign/keyword/search-term/store data.
-- Also creates sync_runs (audit trail) and sync_schedules (cron-like recurring).
--
-- All data tables use UNIQUE indexes for upsert (ON CONFLICT DO UPDATE) so
-- repeated syncs overwrite stale rows instead of duplicating them.
-- All client_id FKs cascade on delete (GDPR chain from migration 008).

-- ── 1. Extend jobs for non-scan usage ────────────────────────────────

ALTER TABLE jobs ALTER COLUMN scan_id DROP NOT NULL;

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS client_id UUID REFERENCES clients(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_jobs_client_id
  ON jobs(client_id)
  WHERE client_id IS NOT NULL;

-- ── 2. Sync run tracking ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS sync_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  connection_id UUID NOT NULL REFERENCES oauth_connections(id) ON DELETE CASCADE,
  sync_type VARCHAR(50) NOT NULL,       -- 'google_ads_campaigns', 'google_ads_keywords', etc.
  status VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending, running, completed, failed
  date_from DATE,
  date_to DATE,
  config JSONB DEFAULT '{}',
  stats JSONB DEFAULT '{}',             -- {rows_fetched, accounts_synced, errors, duration_s}
  started_at TIMESTAMP,
  completed_at TIMESTAMP,
  error_message TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sync_runs_client
  ON sync_runs(client_id, sync_type, created_at DESC);

-- ── 3. Google Ads campaign performance (daily) ───────────────────────

CREATE TABLE IF NOT EXISTS gads_campaigns (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  customer_id VARCHAR(20) NOT NULL,     -- Google Ads customer ID (brand account)
  campaign_id BIGINT NOT NULL,
  campaign_name VARCHAR(500),
  channel_type VARCHAR(50),             -- SEARCH, DISPLAY, PERFORMANCE_MAX, VIDEO, etc.
  status VARCHAR(20),
  date DATE NOT NULL,
  -- Core metrics (typed for aggregation)
  impressions BIGINT DEFAULT 0,
  clicks BIGINT DEFAULT 0,
  cost_micros BIGINT DEFAULT 0,
  conversions FLOAT DEFAULT 0,
  conversions_value FLOAT DEFAULT 0,
  all_conversions FLOAT DEFAULT 0,
  all_conversions_value FLOAT DEFAULT 0,
  ctr FLOAT,
  avg_cpc FLOAT,
  avg_cpm FLOAT,
  abs_top_impr_pct FLOAT,
  top_impr_pct FLOAT,
  optimization_score FLOAT,
  bidding_strategy VARCHAR(50),
  budget_micros BIGINT,
  -- Overflow for rare fields
  raw_data JSONB DEFAULT '{}',
  sync_run_id UUID REFERENCES sync_runs(id) ON DELETE SET NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_gads_campaigns_unique
  ON gads_campaigns(client_id, customer_id, campaign_id, date);

CREATE INDEX IF NOT EXISTS idx_gads_campaigns_lookup
  ON gads_campaigns(client_id, date, customer_id);

-- ── 4. Google Ads keyword performance (daily) ────────────────────────

CREATE TABLE IF NOT EXISTS gads_keywords (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  customer_id VARCHAR(20) NOT NULL,
  campaign_id BIGINT NOT NULL,
  campaign_name VARCHAR(500),
  ad_group_id BIGINT,
  ad_group_name VARCHAR(500),
  keyword_text VARCHAR(500),
  match_type VARCHAR(20),
  criterion_id BIGINT,
  date DATE NOT NULL,
  -- Core metrics
  impressions BIGINT DEFAULT 0,
  clicks BIGINT DEFAULT 0,
  cost_micros BIGINT DEFAULT 0,
  conversions FLOAT DEFAULT 0,
  conversions_value FLOAT DEFAULT 0,
  ctr FLOAT,
  avg_cpc FLOAT,
  quality_score INTEGER,
  -- Impression share
  search_impr_share FLOAT,
  search_abs_top_impr_share FLOAT,
  search_top_impr_share FLOAT,
  search_click_share FLOAT,
  -- Overflow
  raw_data JSONB DEFAULT '{}',
  sync_run_id UUID REFERENCES sync_runs(id) ON DELETE SET NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_gads_keywords_unique
  ON gads_keywords(client_id, customer_id, COALESCE(criterion_id, 0), date);

CREATE INDEX IF NOT EXISTS idx_gads_keywords_lookup
  ON gads_keywords(client_id, date, customer_id);

-- ── 5. Google Ads search terms (daily) ───────────────────────────────

CREATE TABLE IF NOT EXISTS gads_search_terms (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  customer_id VARCHAR(20) NOT NULL,
  campaign_id BIGINT,
  campaign_name VARCHAR(500),
  search_term VARCHAR(1000),
  keyword_text VARCHAR(500),
  keyword_match_type VARCHAR(20),
  search_term_match_type VARCHAR(30),
  date DATE NOT NULL,
  -- Core metrics
  impressions BIGINT DEFAULT 0,
  clicks BIGINT DEFAULT 0,
  cost_micros BIGINT DEFAULT 0,
  conversions FLOAT DEFAULT 0,
  conversions_value FLOAT DEFAULT 0,
  ctr FLOAT,
  avg_cpc FLOAT,
  -- Overflow
  raw_data JSONB DEFAULT '{}',
  sync_run_id UUID REFERENCES sync_runs(id) ON DELETE SET NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_gads_search_terms_lookup
  ON gads_search_terms(client_id, date, customer_id);

-- ── 6. Google Ads per-store performance (daily) ──────────────────────

CREATE TABLE IF NOT EXISTS gads_store_performance (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  customer_id VARCHAR(20) NOT NULL,
  campaign_id BIGINT,
  campaign_name VARCHAR(500),
  channel_type VARCHAR(50),
  place_id VARCHAR(100) NOT NULL,
  business_name VARCHAR(500),
  address VARCHAR(500),
  city VARCHAR(255),
  postal_code VARCHAR(20),
  date DATE NOT NULL,
  -- Store-specific metrics (all_conversions_from_location_asset_*)
  eligible_impressions FLOAT DEFAULT 0,
  store_visits FLOAT DEFAULT 0,
  click_to_call FLOAT DEFAULT 0,
  directions FLOAT DEFAULT 0,
  website_clicks FLOAT DEFAULT 0,
  other_engagement FLOAT DEFAULT 0,
  orders FLOAT DEFAULT 0,
  menu_clicks FLOAT DEFAULT 0,
  -- View-through equivalents
  vtc_store_visits FLOAT DEFAULT 0,
  vtc_click_to_call FLOAT DEFAULT 0,
  vtc_directions FLOAT DEFAULT 0,
  vtc_website FLOAT DEFAULT 0,
  -- Overflow
  raw_data JSONB DEFAULT '{}',
  sync_run_id UUID REFERENCES sync_runs(id) ON DELETE SET NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_gads_store_unique
  ON gads_store_performance(client_id, customer_id, place_id, COALESCE(campaign_id, 0), date);

CREATE INDEX IF NOT EXISTS idx_gads_store_lookup
  ON gads_store_performance(client_id, date, place_id);

-- ── 7. Recurring sync schedules ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS sync_schedules (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  connection_id UUID NOT NULL REFERENCES oauth_connections(id) ON DELETE CASCADE,
  sync_type VARCHAR(50) NOT NULL,
  cron_expression VARCHAR(50) NOT NULL,  -- '0 3 * * *' = daily at 3AM UTC
  is_active BOOLEAN DEFAULT TRUE,
  last_run_at TIMESTAMP,
  next_run_at TIMESTAMP,
  config JSONB DEFAULT '{}',            -- {lookback_days, customer_ids, ...}
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_schedules_unique
  ON sync_schedules(client_id, connection_id, sync_type);
