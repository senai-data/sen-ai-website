-- 055_scan_youtube_creators.sql
--
-- Sprint 10 (YouTube Creator Mapping) - feature #10 from
-- project_10_action_features.md. For each scan we surface the YouTube
-- channels LLMs already cite when answering buyer questions for this
-- brand or its competitors, so the user gets a shortlist of creators to
-- engage with (sponsor / collaborate / pitch).
--
-- Source of URLs : scan_llm_results.citations[] for this scan, filtered
-- to youtube.com / youtu.be hosts. Same architectural choice as S7/S8/S9
-- - we mine what LLMs already cite, no SERP / discovery API in v1.
--
-- Channel grouping : YouTube watch URLs (watch?v=XYZ / youtu.be/XYZ)
-- don't carry channel info in the URL itself. We enrich via YouTube's
-- free public oEmbed endpoint (no API key, no documented quota at our
-- volumes) :
--   GET https://www.youtube.com/oembed?url=<video>&format=json
-- which returns title + author_name + author_url. The author_url is the
-- canonical channel URL (e.g. https://www.youtube.com/@MarieClaireFR)
-- and serves as our grouping key.
--
-- One row per (scan, channel_url). Per-channel aggregates :
--   - citation_count    : total LLM citations of videos on this channel
--   - video_count       : distinct video URLs
--   - competitor_brands : classified competitors mentioned alongside
--                         videos on this channel (filtered set, same
--                         logic as Sprint 9.1)
--   - target_cited      : focus brand mentioned alongside any video here
--   - top_videos JSONB  : up to 8 videos with title / id / contexte /
--                         winning_questions / per-video brand mentions
--   - leverage_score    : 0-100 composite
--
-- classification (mirror of S9 PR outreach) :
--   competitor_only : ≥1 competitor cited, target not cited (= pitch)
--   shared          : both cited (= defend visibility / co-sponsor)
--   target_only     : only target cited (= existing creator advocate)
--
-- Videos that fail oEmbed (private, deleted, age-restricted, removed
-- by uploader) are still counted in citation_count + listed in top_videos
-- with fetch_status set so the UI can chip them as 'unavailable'.

CREATE TABLE IF NOT EXISTS scan_youtube_creators (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id                UUID NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    channel_url            TEXT NOT NULL,
    channel_name           TEXT,
    channel_handle         TEXT,
    citation_count         INTEGER NOT NULL DEFAULT 0,
    video_count            INTEGER NOT NULL DEFAULT 0,
    competitor_brands      TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    target_cited           BOOLEAN NOT NULL DEFAULT false,
    classification         TEXT,
    top_videos             JSONB NOT NULL DEFAULT '[]'::jsonb,
    winning_questions      JSONB NOT NULL DEFAULT '[]'::jsonb,
    leverage_score         INTEGER,
    fetched_at             TIMESTAMP NOT NULL DEFAULT NOW(),
    created_at             TIMESTAMP NOT NULL DEFAULT NOW(),

    UNIQUE (scan_id, channel_url),
    CONSTRAINT syc_classification_values CHECK (
      classification IS NULL OR classification IN ('competitor_only', 'shared', 'target_only')
    ),
    CONSTRAINT syc_leverage_score_range CHECK (
      leverage_score IS NULL OR (leverage_score >= 0 AND leverage_score <= 100)
    )
);

CREATE INDEX IF NOT EXISTS idx_syc_scan          ON scan_youtube_creators(scan_id);
CREATE INDEX IF NOT EXISTS idx_syc_scan_leverage ON scan_youtube_creators(scan_id, leverage_score DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_syc_scan_class    ON scan_youtube_creators(scan_id, classification);

COMMENT ON TABLE scan_youtube_creators IS
    'Sprint 10 YouTube creator mapping. One row per (scan, channel) where '
    'channel is enriched via YouTube oEmbed from each cited video URL. '
    'Excludes Reddit / Wikipedia / brand-own coverage (Sprints 4/7/8). '
    'See worker/handlers/audit_youtube_creators.py + '
    'project_10_action_features.md #10.';

COMMENT ON COLUMN scan_youtube_creators.channel_url IS
    'Canonical YouTube channel URL as returned by oEmbed author_url, e.g. '
    '"https://www.youtube.com/@MarieClaireFR". Stored as-is for click-through.';

COMMENT ON COLUMN scan_youtube_creators.top_videos IS
    'JSONB array of {video_id, url, title, contexte, citation_count, '
    'competitor_brands, target_cited, winning_questions, oembed_status}. '
    'Capped at 8 entries, ordered competitor-cites-first then by citation_count.';

COMMENT ON COLUMN scan_youtube_creators.leverage_score IS
    'Composite 0-100. 40 pts citation engagement, 30 pts competitor breadth, '
    '10 pts novelty (target NOT cited too), 20 pts catalogue richness '
    '(video_count, rewards channels with multiple cited videos).';
