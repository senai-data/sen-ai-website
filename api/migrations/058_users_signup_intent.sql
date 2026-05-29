-- 058_users_signup_intent.sql
--
-- Sprint 15.1 - persist signup intent on the user record so an agency
-- account stays framed as agency across re-logins, not only on the
-- initial URL-carried `?intent=agency` flow.
--
-- Allowed values today : NULL (default - no intent declared) or 'agency'.
-- The set is open so future intents (e.g. 'enterprise', 'freelance')
-- can land without a new migration ; the consumer code lists the values
-- it knows.
--
-- Additive only. No backfill - existing users keep NULL ; admins can
-- promote an existing user via :
--   UPDATE users SET signup_intent = 'agency' WHERE email = '...';

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS signup_intent TEXT;

COMMENT ON COLUMN users.signup_intent IS
    'Sprint 15.1 - captured at first signup from /register?intent=agency '
    'or the OAuth state JWT carrying ?intent=agency. Read by dashboard.astro '
    'to forward to /welcome?intent=agency so the wizard banner stays sticky '
    'across re-logins. Allowed values today : NULL, "agency". Future intents '
    'are accepted by the column - the consumers list what they recognise.';
