-- =========================================================================
-- Phase 1: scan-as-brand refactor
-- =========================================================================
-- This migration adds per-scan brand classification capability without
-- breaking existing functionality. The client_brands.category column is
-- kept for lazy deprecation.
--
-- Deploy order: 1) psql -f 001_scan_as_brand.sql
--               2) restart senai-api (models.py reflects new schema)
--               3) psql -f 002_scan_as_brand_backfill.sql
--               4) restart senai-worker
--               5) docker compose restart nginx
-- =========================================================================

BEGIN;

-- 1.1 Extend `scans` with brand-tracker fields
ALTER TABLE scans
  ADD COLUMN IF NOT EXISTS name              VARCHAR(255),
  ADD COLUMN IF NOT EXISTS focus_brand_id    UUID REFERENCES client_brands(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS parent_scan_id    UUID REFERENCES scans(id)         ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS schedule          VARCHAR(20) DEFAULT 'manual',  -- manual | weekly | monthly
  ADD COLUMN IF NOT EXISTS next_run_at       TIMESTAMP,
  ADD COLUMN IF NOT EXISTS run_index         INTEGER DEFAULT 1;             -- 1 = initial, 2+ = rescan

CREATE INDEX IF NOT EXISTS idx_scans_parent_scan_id  ON scans(parent_scan_id);
CREATE INDEX IF NOT EXISTS idx_scans_focus_brand_id  ON scans(focus_brand_id);
CREATE INDEX IF NOT EXISTS idx_scans_next_run_at     ON scans(next_run_at) WHERE next_run_at IS NOT NULL;

-- 1.2 NEW: per-scan brand classification table
CREATE TABLE IF NOT EXISTS scan_brand_classifications (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scan_id         UUID NOT NULL REFERENCES scans(id)         ON DELETE CASCADE,
  brand_id        UUID NOT NULL REFERENCES client_brands(id) ON DELETE CASCADE,
  classification  VARCHAR(20) NOT NULL,
      -- 'my_brand' | 'competitor' | 'ignored' | 'unclassified'
  is_focus        BOOLEAN DEFAULT FALSE,
  classified_by   VARCHAR(20) DEFAULT 'auto',    -- 'auto' | 'claude' | 'user'
  source          VARCHAR(30),                    -- inherited from ClientBrand.detection_source
  created_at      TIMESTAMP DEFAULT NOW(),
  updated_at      TIMESTAMP DEFAULT NOW(),
  UNIQUE (scan_id, brand_id)
);

CREATE INDEX IF NOT EXISTS idx_sbc_scan_id   ON scan_brand_classifications(scan_id);
CREATE INDEX IF NOT EXISTS idx_sbc_brand_id  ON scan_brand_classifications(brand_id);

-- Exactly one focus per scan (partial unique index)
CREATE UNIQUE INDEX IF NOT EXISTS idx_sbc_one_focus_per_scan
  ON scan_brand_classifications(scan_id) WHERE is_focus = TRUE;

-- 1.3 client_brands becomes a thin catalog
-- Keep `category` column for now (lazy deprecation), but the app will stop writing/reading it.
ALTER TABLE client_brands
  ADD COLUMN IF NOT EXISTS canonical_name VARCHAR(255),
  ADD COLUMN IF NOT EXISTS last_seen_at   TIMESTAMP;

COMMIT;

-- Verify
SELECT 'scans' AS table_name, column_name FROM information_schema.columns
  WHERE table_name='scans' AND column_name IN ('name','focus_brand_id','parent_scan_id','schedule','next_run_at','run_index')
UNION ALL
SELECT 'client_brands', column_name FROM information_schema.columns
  WHERE table_name='client_brands' AND column_name IN ('canonical_name','last_seen_at')
UNION ALL
SELECT 'scan_brand_classifications', column_name FROM information_schema.columns
  WHERE table_name='scan_brand_classifications';
