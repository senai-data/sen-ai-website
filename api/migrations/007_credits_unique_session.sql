-- Migration 007: Enforce one credit row per Stripe Checkout Session.
-- Defense-in-depth for H5 (webhook TOCTOU). The application already
-- serializes via lock_client_credits + idempotency check, but two webhook
-- deliveries that race past the existence check would still both insert.
-- A partial UNIQUE index on stripe_session_id makes the duplicate impossible
-- at the DB level. Partial because debits and refunds have NULL session_id
-- and must remain unconstrained.

CREATE UNIQUE INDEX IF NOT EXISTS idx_client_credits_session_unique
  ON client_credits (stripe_session_id)
  WHERE stripe_session_id IS NOT NULL;
