-- Rollback for 001_scan_as_brand.sql
-- ⚠️ WARNING: This will destroy all per-scan brand classification data.
--           Only use in emergency / development.

BEGIN;

DROP TABLE IF EXISTS scan_brand_classifications;

DROP INDEX IF EXISTS idx_scans_parent_scan_id;
DROP INDEX IF EXISTS idx_scans_focus_brand_id;
DROP INDEX IF EXISTS idx_scans_next_run_at;

ALTER TABLE scans
  DROP COLUMN IF EXISTS name,
  DROP COLUMN IF EXISTS focus_brand_id,
  DROP COLUMN IF EXISTS parent_scan_id,
  DROP COLUMN IF EXISTS schedule,
  DROP COLUMN IF EXISTS next_run_at,
  DROP COLUMN IF EXISTS run_index;

ALTER TABLE client_brands
  DROP COLUMN IF EXISTS canonical_name,
  DROP COLUMN IF EXISTS last_seen_at;

COMMIT;
