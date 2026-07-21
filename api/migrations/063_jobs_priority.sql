-- 063_jobs_priority.sql
--
-- Worker queue priority (diagnostic 2026-07-21, cf project_worker_queue_scaling).
--
-- The single scan-worker polled jobs pure-FIFO by created_at, so a background
-- sweep enqueued earlier (discover_media_catalog, rate-limited on Babbar with
-- 52s pauses) sat in front of a user's run_llm_tests and the rescan looked
-- stuck at 0%. Add a priority band so user-waited scan work always jumps ahead
-- of background maintenance.
--
--   200 = user-triggered scan work a human is waiting on (run_llm_tests at
--         launch / rescan).
--   100 = neutral default (post-scan analytical chain, setup pipeline, manual
--         audit /refresh, content gen, everything else).
--    50 = background maintenance with nobody waiting at enqueue time
--         (discover_media_catalog, measure_publish_outcome, T+14
--         refresh_ai_snapshot sweep, purge_stale_pages) and the auto-chained
--         non-blocking post-scan audits (they run after the scan is already
--         flipped 'completed', so they never gate the visible result).
--
-- Higher = picked first. Poll: ORDER BY priority DESC, created_at.
-- Additive + idempotent: existing rows inherit the neutral 100 default, so the
-- code deploy order after this migration cannot break anything.

ALTER TABLE jobs ADD COLUMN IF NOT EXISTS priority smallint NOT NULL DEFAULT 100;

COMMENT ON COLUMN jobs.priority IS
    'Poll ordering weight, higher picked first. 200 = user-waited scan work '
    '(run_llm_tests), 100 = neutral default, 50 = background sweeps and '
    'non-blocking post-scan audits. See migration 063 + project_worker_queue_scaling.';

-- Partial index backing the worker poll (WHERE status=''pending'' ORDER BY
-- priority DESC, created_at). Scoped to pending rows so it stays tiny as
-- completed jobs accumulate - the hot path never scans terminal rows.
CREATE INDEX IF NOT EXISTS idx_jobs_poll
    ON jobs (priority DESC, created_at)
    WHERE status = 'pending';
