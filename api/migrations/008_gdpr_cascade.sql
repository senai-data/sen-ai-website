-- Migration 008: Cascade deletes for GDPR account deletion (H7).
--
-- Self-service account deletion (DELETE /api/auth/me) needs to be able to
-- drop a user and any clients they solo-own, with all the dependent data
-- (scans, brands, credits, etc.) cleaned up atomically. Currently 6 of 7
-- FKs to clients and 2 of 2 FKs to users have NO ACTION, which would block
-- the deletion with FK violations.
--
-- After this migration:
--   * Deleting a user → user_clients rows cascade away, scans.created_by
--     becomes NULL (preserve scan as business data, anonymize audit trail).
--   * Deleting a client → ALL its dependent rows cascade away, including
--     scans (which already cascade-delete their own children: keywords,
--     personas, questions, llm_results, opportunities, jobs, content items,
--     brand_classifications, brand_topics).
--
-- The CASCADE chain is end-to-end so a single DELETE FROM clients WHERE id=X
-- atomically wipes the entire footprint of that client.

-- ── User-side cascades ─────────────────────────────────────────────────

ALTER TABLE user_clients DROP CONSTRAINT user_clients_user_id_fkey;
ALTER TABLE user_clients ADD CONSTRAINT user_clients_user_id_fkey
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE;

ALTER TABLE scans DROP CONSTRAINT scans_created_by_fkey;
ALTER TABLE scans ADD CONSTRAINT scans_created_by_fkey
  FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL;

-- ── Client-side cascades ───────────────────────────────────────────────

ALTER TABLE user_clients DROP CONSTRAINT user_clients_client_id_fkey;
ALTER TABLE user_clients ADD CONSTRAINT user_clients_client_id_fkey
  FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE;

ALTER TABLE scans DROP CONSTRAINT scans_client_id_fkey;
ALTER TABLE scans ADD CONSTRAINT scans_client_id_fkey
  FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE;

ALTER TABLE client_brands DROP CONSTRAINT client_brands_client_id_fkey;
ALTER TABLE client_brands ADD CONSTRAINT client_brands_client_id_fkey
  FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE;

ALTER TABLE client_modules DROP CONSTRAINT client_modules_client_id_fkey;
ALTER TABLE client_modules ADD CONSTRAINT client_modules_client_id_fkey
  FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE;

ALTER TABLE client_api_keys DROP CONSTRAINT client_api_keys_client_id_fkey;
ALTER TABLE client_api_keys ADD CONSTRAINT client_api_keys_client_id_fkey
  FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE;

ALTER TABLE subscriptions DROP CONSTRAINT subscriptions_client_id_fkey;
ALTER TABLE subscriptions ADD CONSTRAINT subscriptions_client_id_fkey
  FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE;
