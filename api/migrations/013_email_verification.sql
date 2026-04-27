-- H3: Email verification — gate welcome bonus behind verified email.
-- Google OAuth users are auto-verified. Email/password users must click a link.

ALTER TABLE users ADD COLUMN is_email_verified BOOLEAN NOT NULL DEFAULT FALSE;

-- Backfill: existing users are considered verified (they're already active)
UPDATE users SET is_email_verified = TRUE;
