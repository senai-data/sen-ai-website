-- Migration 017: Client deliverable reports
-- Static HTML reports published to https://sen-ai.fr/r/{slug}/{filename}.html
-- via the superadmin UI at /app/admin/reports.
--
-- Files live on disk at /opt/sen-ai/reports/{client}/{period}/{slug}/{filename}.html
-- with a flat symlink under /opt/sen-ai/reports/_serve/{slug}/ that Nginx serves.
-- This table is the source of truth for "what is currently published".

BEGIN;

CREATE TABLE IF NOT EXISTS reports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug            VARCHAR(12)  NOT NULL UNIQUE,           -- 12-char crypto-random alnum, used in public URL
    filename        VARCHAR(255) NOT NULL,                  -- slugified, e.g. "rapport-q2.html"
    client_label    VARCHAR(100) NOT NULL,                  -- free-text consulting client, e.g. "pierrefabre"
    period_label    VARCHAR(100) NOT NULL,                  -- free-text period, e.g. "avril-2026"
    real_path       TEXT         NOT NULL,                  -- absolute path on VPS (for unpublish)
    file_size       INTEGER      NOT NULL,                  -- bytes
    uploaded_by     UUID         REFERENCES users(id) ON DELETE SET NULL,
    published_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ  NOT NULL,                  -- soft-expiry (UI hint, not enforced by Nginx)
    unpublished_at  TIMESTAMPTZ                             -- NULL = active; set when removed from disk
);

-- Active reports (most common query)
CREATE INDEX IF NOT EXISTS idx_reports_active
    ON reports (published_at DESC)
    WHERE unpublished_at IS NULL;

-- Filter / autocomplete by client + period
CREATE INDEX IF NOT EXISTS idx_reports_client_period
    ON reports (client_label, period_label);

COMMIT;
