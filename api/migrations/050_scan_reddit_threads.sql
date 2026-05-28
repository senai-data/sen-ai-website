-- 050_scan_reddit_threads.sql
--
-- Sprint 8 (Reddit / Forum Opportunity Finder) - feature #3 from
-- project_10_action_features.md. For each scan we surface the Reddit
-- threads the LLMs already cite, audit each one for brand mentions,
-- classify them as "competitor_wins" (where a competitor was discussed
-- and the user's brand wasn't), score them by engagement + leverage,
-- and run a per-thread Haiku sentiment pass so the user knows whether
-- joining the conversation is opportunity or risk.
--
-- Source of URLs : ONLY Reddit links from scan_llm_results.citations[]
-- this scan. No SERP discovery in v1 - we mine what wins right now.
-- Same architectural choice as Sprint 7 (competitor pages).
--
-- top_comments JSONB shape :
--   [{ "author": "u/...", "body": "...", "score": 12, "depth": 0 }, ...]
--   limited to top 5 by upvotes, depth 0 (root comments) preferred.
--
-- competitors_mentioned TEXT[] : canonical brand names from the scan's
-- client_brands rows that we regex-matched on (title + body + comments).
--
-- classification :
--   competitor_wins : >= 1 competitor mentioned, target absent  (= opportunity)
--   you_win         : target brand mentioned                     (= positive presence)
--   neutral         : neither mentioned                          (= context noise)
--
-- sentiment + sentiment_summary : populated by Haiku per thread,
-- describes the overall sentiment toward the discussed brand(s). NULL
-- when the LLM sentiment pass wasn't run (--no-sentiment flag or budget cap).
--
-- leverage_score (0-100) composite :
--   55 pts  engagement = log10(score + 1) × log10(num_comments + 1) normalized
--   25 pts  classification : competitor_wins=+25, neutral=+10, you_win=+0
--   20 pts  sentiment lever : negative-about-competitor=+20, neutral=+10,
--           positive-about-competitor=+0 (you don't fix what they're winning at)

CREATE TABLE IF NOT EXISTS scan_reddit_threads (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id                 UUID NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    url                     TEXT NOT NULL,
    subreddit               TEXT,
    title                   TEXT,
    posted_at               TIMESTAMP,
    author                  TEXT,
    score                   INTEGER,
    num_comments            INTEGER,
    fetched_at              TIMESTAMP NOT NULL DEFAULT NOW(),
    fetch_status            INTEGER,
    fetch_error             TEXT,
    citation_count          INTEGER NOT NULL DEFAULT 0,
    target_mentioned        BOOLEAN NOT NULL DEFAULT false,
    competitors_mentioned   TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    classification          TEXT,
    sentiment               TEXT,
    sentiment_summary       TEXT,
    body_excerpt            TEXT,
    top_comments            JSONB NOT NULL DEFAULT '[]'::jsonb,
    winning_questions       JSONB NOT NULL DEFAULT '[]'::jsonb,
    leverage_score          INTEGER,
    created_at              TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE (scan_id, url),
    CONSTRAINT rt_leverage_score_range CHECK (
      leverage_score IS NULL OR (leverage_score >= 0 AND leverage_score <= 100)
    ),
    CONSTRAINT rt_classification_values CHECK (
      classification IS NULL OR classification IN ('competitor_wins', 'you_win', 'neutral')
    ),
    CONSTRAINT rt_sentiment_values CHECK (
      sentiment IS NULL OR sentiment IN ('positive', 'negative', 'neutral', 'mixed')
    )
);

CREATE INDEX IF NOT EXISTS idx_srt_scan          ON scan_reddit_threads(scan_id);
CREATE INDEX IF NOT EXISTS idx_srt_scan_leverage ON scan_reddit_threads(scan_id, leverage_score DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_srt_scan_class    ON scan_reddit_threads(scan_id, classification);

COMMENT ON TABLE scan_reddit_threads IS
    'Sprint 8 Reddit opportunity finder. One row per (scan, Reddit URL) where '
    'URL is a Reddit thread cited by at least one LLM during the scan. We fetch '
    'the thread via Reddit''s public JSON endpoint (<url>.json) - no scraping, '
    'no OAuth, polite 1 req/s rate. Classification + sentiment + leverage score '
    'drive the UI sort order. See worker/handlers/audit_reddit_threads.py + '
    'project_10_action_features.md #3.';
