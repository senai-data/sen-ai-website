-- Migration 011: Feature gating — Client.apps JSONB column.
--
-- Each client has an `apps` object that controls which platform modules
-- are visible and accessible. Default: only AI Scan enabled (the
-- self-service product). Superadmin toggles other apps via
-- PATCH /api/admin/clients/{id}/apps.
--
-- Schema per app key:
--   { "enabled": true, ...optional config }
--
-- Examples:
--   {"ai_scan": {"enabled": true}}                          — default (self-service)
--   {"ai_scan": {"enabled": true}, "google_ads": {"enabled": true}}  — Pierre Fabre
--
-- Backfill: all existing clients get the default (ai_scan only).

ALTER TABLE clients
  ADD COLUMN IF NOT EXISTS apps JSONB NOT NULL DEFAULT '{"ai_scan": {"enabled": true}}';
