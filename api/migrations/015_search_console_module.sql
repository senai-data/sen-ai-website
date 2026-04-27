-- Migration 015: Google Search Console module (Phase 1).
--
-- Creates the core tables for GSC query/page data, topic clustering,
-- page-topic mapping, and newsletter storage.
--
-- Reuses sync_runs (from migration 012) for audit trail.
-- All client_id FKs cascade on delete (GDPR chain from migration 008).
-- Data tables use UNIQUE indexes for upsert (ON CONFLICT DO UPDATE).

-- ── 1. GSC query performance (daily, dimensions: date + query) ──────

CREATE TABLE IF NOT EXISTS gsc_queries (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  domain VARCHAR(500) NOT NULL,       -- GSC site URL (e.g. "sc-domain:example.com")
  date DATE NOT NULL,
  query VARCHAR(1000) NOT NULL,       -- search query text
  clicks BIGINT DEFAULT 0,
  impressions BIGINT DEFAULT 0,
  ctr FLOAT,
  position FLOAT,
  sync_run_id UUID REFERENCES sync_runs(id) ON DELETE SET NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_gsc_queries_unique
  ON gsc_queries(client_id, domain, date, query);

CREATE INDEX IF NOT EXISTS idx_gsc_queries_lookup
  ON gsc_queries(client_id, domain, date);

-- ── 2. GSC page performance (daily, dimensions: date + query + page) ─

CREATE TABLE IF NOT EXISTS gsc_pages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  domain VARCHAR(500) NOT NULL,
  date DATE NOT NULL,
  query VARCHAR(1000) NOT NULL,
  page TEXT NOT NULL,                  -- full URL
  page_hash VARCHAR(32) NOT NULL,     -- md5(page) for indexing (avoids B-tree width limit)
  clicks BIGINT DEFAULT 0,
  impressions BIGINT DEFAULT 0,
  ctr FLOAT,
  position FLOAT,
  sync_run_id UUID REFERENCES sync_runs(id) ON DELETE SET NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_gsc_pages_unique
  ON gsc_pages(client_id, domain, date, query, page_hash);

CREATE INDEX IF NOT EXISTS idx_gsc_pages_lookup
  ON gsc_pages(client_id, domain, date);

-- ── 3. GSC topics (Phase 3 — created now for forward compatibility) ──

CREATE TABLE IF NOT EXISTS gsc_topics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  domain VARCHAR(500) NOT NULL,
  name VARCHAR(255) NOT NULL,
  description TEXT,
  example_urls TEXT[],                 -- top representative URLs
  is_active BOOLEAN DEFAULT TRUE,     -- user toggle for validation
  page_count INTEGER DEFAULT 0,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_gsc_topics_unique
  ON gsc_topics(client_id, domain, name);

-- ── 4. GSC page-topic mapping (URL → topic) ─────────────────────────

CREATE TABLE IF NOT EXISTS gsc_page_topics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  domain VARCHAR(500) NOT NULL,
  page_url TEXT NOT NULL,
  topic_id UUID NOT NULL REFERENCES gsc_topics(id) ON DELETE CASCADE,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_gsc_page_topics_unique
  ON gsc_page_topics(client_id, domain, md5(page_url));

-- ── 5. GSC newsletters (generated HTML reports) ─────────────────────

CREATE TABLE IF NOT EXISTS gsc_newsletters (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  domain VARCHAR(500) NOT NULL,
  month VARCHAR(7) NOT NULL,           -- "2026-03"
  html_content TEXT,
  status VARCHAR(20) DEFAULT 'pending', -- pending, generating, completed, failed
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_gsc_newsletters_unique
  ON gsc_newsletters(client_id, domain, month);
