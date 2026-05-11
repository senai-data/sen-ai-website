-- 022_scan_type.sql
--
-- Persist the user's explicit intent at scan creation : are they measuring
-- their own brand visibility or auditing a competitor ?
--
-- Until now, the wizard offered the toggle ("This domain is — My own site /
-- A competitor's site") but only used it client-side to decide whether to
-- pre-fill target_domains. The scan record itself had no record of the
-- intent, so downstream handlers (classify_topics, materialize_content_items)
-- had to deduce it from heuristics (scan.domain vs client.primary_brand_ids).
-- Heuristics fail in two cases :
--   1. user has no primary_brand_ids yet (new workspace) → falls back to "own"
--   2. user scans their own domain that isn't in primary_brand_ids → false
--      "competitor" classification
--
-- Storing the intent gives us an authoritative signal that survives
-- workspace-config changes.
--
-- Values :
--   'own_brand'        — user is measuring their own brand visibility
--   'competitor_audit' — user is auditing a competitor's visibility
--   NULL               — pre-migration scans or anonymous launches; downstream
--                        code falls back to the heuristic
--
-- Backfill is conservative : we leave existing rows NULL rather than guessing.
-- The heuristic still handles them correctly for the most part.

ALTER TABLE scans
    ADD COLUMN IF NOT EXISTS scan_type TEXT;

COMMENT ON COLUMN scans.scan_type IS
    'User-declared scan intent: own_brand | competitor_audit | NULL '
    '(NULL = pre-migration or unknown, downstream heuristics apply). '
    'See is_competitor_scan() in worker/services/brand_resolver.py.';
