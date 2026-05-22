-- 041_media_feedback.sql
--
-- Phase MR.1 — Suggest Alternative Media (Sprint 1).
--
-- Tracks user decisions on suggest-media suggestions :
--   - 'accepted'  — user clicked Accept; target_url is now set
--   - 'rejected'  — user clicked Reject; this (item, domain) is excluded
--                   from future suggestions for THIS content item
--   - 'replaced'  — accepted then later changed; useful to surface "abandoned"
--                   media domains for catalog-quality dashboards
--
-- Two roles for this table :
--   a) Hard filter — `WHERE action='rejected' AND content_item_id = $1`
--      is the canonical anti-repeat filter in media_replacement.suggest().
--   b) Footprint cap — `WHERE action='accepted' AND client_id = $1 AND domain = $2`
--      count >= FOOTPRINT_CAP (default 3) penalizes scoring for that client.
--   c) Learning signal for Sprint 4 — accepted+published pairs feed
--      media_publish_outcome via the T+14 cron.
--
-- See project_phase_mr1_media_catalog.md.

CREATE TABLE IF NOT EXISTS media_feedback (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id       UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    opportunity_id  UUID REFERENCES scan_opportunities(id) ON DELETE SET NULL,
    content_item_id UUID REFERENCES scan_content_items(id) ON DELETE CASCADE,
    domain          TEXT NOT NULL,
    action          TEXT NOT NULL,
    reason          TEXT,
    ts              TIMESTAMP NOT NULL DEFAULT NOW(),
    CONSTRAINT media_feedback_action_chk
        CHECK (action IN ('accepted', 'rejected', 'replaced'))
);

CREATE INDEX IF NOT EXISTS idx_media_feedback_item_action
    ON media_feedback (content_item_id, action);

CREATE INDEX IF NOT EXISTS idx_media_feedback_client_domain_action
    ON media_feedback (client_id, domain, action);

COMMENT ON TABLE media_feedback IS
    'Phase MR.1 — Per-(item, domain) user decisions on suggest-media output. '
    'Drives anti-repeat filter and per-client footprint cap. '
    'See worker/services/media_replacement.py.';
