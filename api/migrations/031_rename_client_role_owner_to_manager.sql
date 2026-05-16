-- 031_rename_client_role_owner_to_manager.sql
--
-- Phase E.C.5.1 — Disambiguate the role vocabulary.
--
-- Before this migration the literal string 'owner' appeared on TWO unrelated
-- role columns :
--   • organization_users.role  — org-level (owner/admin/member)
--   • org_user_clients.role    — per-client (owner/editor/viewer)
--   • user_clients.role        — per-client (owner/editor/viewer, LEGACY)
--
-- The clash made the members page UI confusing : "Owner" meant agency-boss
-- in one column and workspace-boss in another. We rename the per-client
-- value 'owner' to 'manager' so the two scopes never collide again.
--
-- The PG ENUM `user_role` underlying `user_clients.role` is renamed
-- atomically — postgres updates the type AND every dependent row in
-- a single ALTER. The `org_user_clients.role` column is plain TEXT so
-- we do an explicit UPDATE.
--
-- Idempotency : a second run will fail on the ALTER (value 'owner'
-- no longer exists) — that's fine, the migration is one-shot.

BEGIN;

-- Path 1 : PG ENUM type backing user_clients.role
-- Postgres 10+ supports ALTER TYPE ... RENAME VALUE inside a transaction.
ALTER TYPE user_role RENAME VALUE 'owner' TO 'manager';

-- Path 2 : plain TEXT column on org_user_clients
UPDATE org_user_clients SET role = 'manager' WHERE role = 'owner';

COMMIT;
