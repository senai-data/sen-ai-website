-- 045_scan_llm_results_run_index.sql
--
-- Sprint N-runs (project plan lovely-skipping-sunset) - Statistical multi-sampling.
--
-- Why: LLMs at temperature > 0 vary 10-34% between identical runs (Goyal 2024,
-- Lampinen 2023). A single tir = noise. To compute reliable "% cited" rates
-- with a measurable margin of error, we run each question N times (default 10).
--
-- Schema change: one row per (scan, question, provider, run_index).
--
-- Layout convention :
--   run_index IN [1..N]  →  one actual LLM call. Carries response_text,
--                            citations, duration_ms, tokens, and a
--                            cheap regex-derived target_cited bool.
--                            brand_analysis = NULL (skipped per-run for cost).
--
--   run_index = 0        →  consensus row. response_text = NULL,
--                            citations = NULL, but brand_analysis JSONB is
--                            populated by a single EntityAnalyzer pass over
--                            the concatenated N responses. Carries the
--                            sentiment/position/recommandation signals.
--                            This is the row that judge_question_responses
--                            reads (one judgment per question/provider, not
--                            N judgments).
--
-- Backward compat: existing rows get run_index = 1 via DEFAULT. No consumer
-- breaks because all current code reads "one row per (scan, q, provider)"
-- and that semantic still holds for scans with runs_depth=1 (default).
--
-- Aggregation contract for the UI :
--   SELECT AVG(target_cited::int) AS mention_rate
--   FROM scan_llm_results
--   WHERE scan_id = :id AND run_index > 0
--   GROUP BY question_id, provider;
--
-- See project plan: C:/Users/leed/.claude/plans/lovely-skipping-sunset.md

ALTER TABLE scan_llm_results ADD COLUMN run_index INT NOT NULL DEFAULT 1;

CREATE INDEX idx_scan_llm_results_scan_q_prov_run
    ON scan_llm_results(scan_id, question_id, provider, run_index);

COMMENT ON COLUMN scan_llm_results.run_index IS
    'Run number within a multi-sampling scan. run_index >= 1 = a real LLM call '
    '(response_text + citations populated). run_index = 0 = consensus row '
    '(response_text NULL, brand_analysis populated from EntityAnalyzer over '
    'the concatenated N responses). Default 1 = legacy single-run scan. '
    'See migration 045 + project plan lovely-skipping-sunset.md.';
