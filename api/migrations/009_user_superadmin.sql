-- Migration 009: Add is_superadmin flag to users.
--
-- M5: foundation for the superadmin role and the /api/admin/* routes.
-- Superadmins are platform operators (us, the dev team) — they can list
-- all clients, see GDPR data on behalf of users in support cases, toggle
-- feature flags (Phase 0 OAuth roadmap: enable apps per client), and
-- access the admin UI at /app/admin/.
--
-- Default false: existing users are NOT superadmins. data@sen-ai.fr will
-- be flipped to true manually after deployment via:
--   UPDATE users SET is_superadmin = true WHERE email = 'data@sen-ai.fr';

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS is_superadmin BOOLEAN NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_users_superadmin
  ON users (is_superadmin)
  WHERE is_superadmin = true;
