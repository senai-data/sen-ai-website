-- 038_client_brand_canonical_unique.sql
--
-- Root-cause fix for the brand duplicates problem reported 2026-05-19 :
-- "Pierre Fabre workspace had 3 separate rows for the same brand
-- (Eau Thermale Avène / Eau thermale Avène / Avène), each with its own
-- children." Cause : every ClientBrand creation site (`cleanup_brands`,
-- `generate_domain_brief`, `classify_topics`, `detect_competitors`,
-- `import_competitors_from_brief`) was setting `canonical_name = name`
-- without normalisation, so two LLM responses that disagreed on case or
-- accents produced two distinct rows.
--
-- This migration :
--   1. Enables the `unaccent` extension.
--   2. Normalises every existing canonical_name to lower(unaccent(...)).
--   3. Merges duplicates (keeper = most children, then oldest id) :
--        - reparent children
--        - reassign scan_brand_classifications (skip when keeper already
--          has an SBC for that scan — that means the conflict is
--          legitimate and we keep the canonical)
--        - reassign focus_brand_id on scans
--        - replace loser_id with keeper_id inside client.primary_brand_ids,
--          dedupe the resulting array
--        - delete loser rows
--   4. Adds a UNIQUE index on (client_id, canonical_name) so future
--      INSERT attempts with a colliding normalised name will fail loud
--      (each call site now normalises via worker/api services/brand_name_norm.py
--      before INSERT — see commit 2026-05-19 follow-up).

CREATE EXTENSION IF NOT EXISTS unaccent;

-- ─── 1. Normalise existing canonical_name ───────────────────────────────
UPDATE client_brands
SET canonical_name = LOWER(unaccent(canonical_name))
WHERE canonical_name IS NOT NULL
  AND canonical_name <> LOWER(unaccent(canonical_name));

-- ─── 2. Build remap table (keeper ← losers per client) ──────────────────
CREATE TEMP TABLE _brand_remap AS
WITH ranked AS (
    SELECT cb.id, cb.client_id, cb.canonical_name,
           (SELECT COUNT(*) FROM client_brands ch WHERE ch.parent_id = cb.id) AS n_children,
           ROW_NUMBER() OVER (
               PARTITION BY cb.client_id, cb.canonical_name
               ORDER BY
                   (SELECT COUNT(*) FROM client_brands ch WHERE ch.parent_id = cb.id) DESC,
                   cb.first_detected_at ASC NULLS LAST,
                   cb.id ASC
           ) AS rk
    FROM client_brands cb
    WHERE cb.canonical_name IS NOT NULL
),
keepers AS (SELECT id, client_id, canonical_name FROM ranked WHERE rk = 1),
losers  AS (SELECT id, client_id, canonical_name FROM ranked WHERE rk > 1)
SELECT l.id AS loser_id, k.id AS keeper_id, l.client_id
FROM losers l
JOIN keepers k ON k.client_id = l.client_id AND k.canonical_name = l.canonical_name;

-- ─── 3a. Reparent children of losers ────────────────────────────────────
UPDATE client_brands cb
SET parent_id = r.keeper_id
FROM _brand_remap r
WHERE cb.parent_id = r.loser_id;

-- ─── 3b. Reassign focus_brand_id on scans pointing to a loser ───────────
UPDATE scans s
SET focus_brand_id = r.keeper_id
FROM _brand_remap r
WHERE s.focus_brand_id = r.loser_id;

-- ─── 3c. Reassign scan_brand_classifications (skip if keeper already
--        has SBC for that scan — redundant rows get dropped in 3d). ─────
UPDATE scan_brand_classifications sbc
SET brand_id = r.keeper_id
FROM _brand_remap r
WHERE sbc.brand_id = r.loser_id
  AND NOT EXISTS (
      SELECT 1 FROM scan_brand_classifications sbc2
      WHERE sbc2.scan_id = sbc.scan_id AND sbc2.brand_id = r.keeper_id
  );

-- ─── 3d. Delete redundant SBC rows (keeper already had one for that scan) ──
DELETE FROM scan_brand_classifications sbc
WHERE EXISTS (SELECT 1 FROM _brand_remap r WHERE r.loser_id = sbc.brand_id);

-- ─── 3e. Rewrite client.primary_brand_ids: replace loser_id with keeper_id,
--        then dedupe via DISTINCT (order is recomputed but kept stable
--        via first-occurrence index in the original array). ──────────────
UPDATE clients c
SET primary_brand_ids = sub.deduped
FROM (
    SELECT c2.id AS client_id,
           (
               SELECT ARRAY(
                   SELECT DISTINCT ON (mapped) mapped
                   FROM (
                       SELECT
                           COALESCE((SELECT keeper_id FROM _brand_remap WHERE loser_id = bid), bid) AS mapped,
                           ord
                       FROM unnest(c2.primary_brand_ids) WITH ORDINALITY AS u(bid, ord)
                   ) m
                   ORDER BY mapped, ord
               )
           ) AS deduped
    FROM clients c2
    WHERE c2.primary_brand_ids IS NOT NULL
      AND c2.primary_brand_ids && (SELECT ARRAY_AGG(loser_id) FROM _brand_remap)
) sub
WHERE c.id = sub.client_id;

-- ─── 3f. Delete loser ClientBrand rows ──────────────────────────────────
DELETE FROM client_brands WHERE id IN (SELECT loser_id FROM _brand_remap);

-- ─── 4. Add the UNIQUE constraint preventing future duplicates ──────────
-- Partial index : canonical_name can still be NULL for legacy rows that
-- predate the cleanup; the constraint only enforces uniqueness when set.
CREATE UNIQUE INDEX IF NOT EXISTS idx_client_brands_canonical_unique
    ON client_brands (client_id, canonical_name)
    WHERE canonical_name IS NOT NULL;

COMMENT ON INDEX idx_client_brands_canonical_unique IS
    'Enforces 1 ClientBrand row per (client_id, normalized canonical_name). '
    'Callers must normalize names via services/brand_name_norm.normalize() before '
    'INSERT — lower + unaccent + trim. See migration 038.';
