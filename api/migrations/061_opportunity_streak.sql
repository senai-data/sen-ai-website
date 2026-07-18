-- 061_opportunity_streak.sql
--
-- Act-scope P4 (plan 2026-06-12, cf project_act_scope_plan) - opportunity
-- streak : qualify the present with history instead of diluting it.
--
--   status  : 'new'        = key absent from the PREVIOUS completed scan of
--                            the lineage (or no previous scan at all).
--             'persisting' = key present in the previous scan too ; the gap
--                            survived a rescan.
--   streak  : number of consecutive completed scans (this one included) in
--             which the key was present. 1 for 'new', prev+1 for 'persisting'.
--   provider: the LLM provider of the (question, provider) group this row was
--             scored from. generate_opportunities has always produced one row
--             per (question, provider) but never persisted the provider - the
--             streak key REQUIRES it (a gemini gap is not an openai gap).
--             NULL = legacy pre-P4 row : streak matching degrades to
--             text-only against those, and their serialized status is null
--             (the UI hides chips rather than showing a false 'New').
--
-- Cross-scan key = (normalized question text lower/trim/collapse-ws,
-- provider). NEVER question_id : rescans copy questions under new ids and
-- imported lineages point at the root's questions.
--
-- Cross model-era matching is a FEATURE : a gap present before AND after a
-- model change is structural - the streak does not reset at P3 boundaries.

ALTER TABLE scan_opportunities ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'new';
ALTER TABLE scan_opportunities ADD COLUMN IF NOT EXISTS streak INT DEFAULT 1;
ALTER TABLE scan_opportunities ADD COLUMN IF NOT EXISTS provider TEXT;

COMMENT ON COLUMN scan_opportunities.status IS
    'new = key absent from the previous completed scan of the lineage ; '
    'persisting = present in the previous scan too. Key = (normalized '
    'question text, provider). See migration 061.';
COMMENT ON COLUMN scan_opportunities.streak IS
    'Consecutive completed scans (current included) in which this '
    '(question text, provider) gap was present. See migration 061.';
COMMENT ON COLUMN scan_opportunities.provider IS
    'LLM provider of the (question, provider) group this opportunity was '
    'scored from. NULL = legacy pre-P4 row. See migration 061.';
