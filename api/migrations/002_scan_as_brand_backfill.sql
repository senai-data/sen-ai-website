-- =========================================================================
-- Phase 1 Backfill: scan-as-brand data migration
-- =========================================================================
-- Run AFTER 001_scan_as_brand.sql + API restart.
-- Idempotent: safe to re-run (guards via ON CONFLICT + WHERE IS NULL).
-- =========================================================================

BEGIN;

-- 2.1 Seed scan_brand_classifications from current client_brands.category
-- For every existing scan, create classifications based on the client's current
-- brand categories so past results stay interpretable.
INSERT INTO scan_brand_classifications (scan_id, brand_id, classification, is_focus, classified_by, source)
SELECT
    s.id,
    b.id,
    CASE
        WHEN b.category IN ('target_brand','target_gamme','target_product') THEN 'my_brand'
        WHEN b.category IN ('competitor','competitor_gamme')                 THEN 'competitor'
        WHEN b.category = 'ignored'                                          THEN 'ignored'
        ELSE 'unclassified'
    END AS classification,
    FALSE AS is_focus,
    'auto' AS classified_by,
    b.detection_source
FROM scans s
JOIN client_brands b ON b.client_id = s.client_id
ON CONFLICT (scan_id, brand_id) DO NOTHING;

-- 2.2 Pick focus brand per scan
-- Priority: target_brand whose name or domain matches scan.domain
--         > first target_brand
--         > NULL (UI will show banner "Please select focus brand")
WITH focus_pick AS (
    SELECT DISTINCT ON (s.id)
           s.id AS scan_id,
           b.id AS brand_id
    FROM scans s
    LEFT JOIN client_brands b
      ON b.client_id = s.client_id
     AND b.category IN ('target_brand','target_gamme')
    WHERE s.focus_brand_id IS NULL  -- idempotent guard: don't override existing
    ORDER BY s.id,
             -- prefer brand whose domain matches the scan domain
             (CASE WHEN b.domain IS NOT NULL AND position(b.domain in s.domain) > 0 THEN 0 ELSE 1 END),
             -- then prefer target_brand over target_gamme
             (CASE WHEN b.category='target_brand' THEN 0 ELSE 1 END),
             b.first_detected_at ASC NULLS LAST
)
UPDATE scan_brand_classifications sbc
SET    is_focus = TRUE
FROM   focus_pick fp
WHERE  sbc.scan_id = fp.scan_id
  AND  sbc.brand_id = fp.brand_id
  AND  NOT EXISTS (
    SELECT 1 FROM scan_brand_classifications sbc2
    WHERE sbc2.scan_id = fp.scan_id AND sbc2.is_focus = TRUE
  );

UPDATE scans s
SET focus_brand_id = fp.brand_id
FROM (
    SELECT scan_id, brand_id FROM scan_brand_classifications WHERE is_focus = TRUE
) fp
WHERE s.id = fp.scan_id AND s.focus_brand_id IS NULL;

-- 2.3 Default scan.name to domain if null (users can rename later)
UPDATE scans SET name = COALESCE(name, domain) WHERE name IS NULL;

-- 2.4 Everybody gets run_index=1 and parent_scan_id=NULL (they are initial runs)
UPDATE scans SET run_index = 1 WHERE run_index IS NULL;

COMMIT;

-- =========================================================================
-- Verification queries — run manually to check backfill results
-- =========================================================================

-- 1. How many scans got a focus brand?
-- SELECT COUNT(*) FILTER (WHERE focus_brand_id IS NOT NULL) AS with_focus,
--        COUNT(*) FILTER (WHERE focus_brand_id IS NULL)     AS without_focus
-- FROM scans;

-- 2. Scans still needing manual focus selection
-- SELECT id, name, domain, status FROM scans WHERE focus_brand_id IS NULL;

-- 3. SBC row count per scan (should be > 0 for most)
-- SELECT s.name, s.domain, s.status, COUNT(sbc.id) AS sbc_count
-- FROM scans s LEFT JOIN scan_brand_classifications sbc ON sbc.scan_id = s.id
-- GROUP BY s.id, s.name, s.domain, s.status
-- ORDER BY sbc_count DESC;

-- 4. Exactly one focus per scan (should return 0 rows = no violations)
-- SELECT scan_id, COUNT(*) FROM scan_brand_classifications
-- WHERE is_focus = TRUE GROUP BY scan_id HAVING COUNT(*) > 1;

-- 5. Distribution of classifications
-- SELECT classification, COUNT(*) FROM scan_brand_classifications
-- GROUP BY classification ORDER BY COUNT(*) DESC;
