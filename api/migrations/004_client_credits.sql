-- Migration 004: Credit ledger for scan + content credits (one-time packs, no subscription).
-- Each row is a transaction. balance_after = running balance for fast reads.

CREATE TABLE IF NOT EXISTS client_credits (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  credit_type VARCHAR(20) NOT NULL CHECK (credit_type IN ('scan', 'content')),
  amount INTEGER NOT NULL,         -- positive = purchase/grant, negative = consumption
  balance_after INTEGER NOT NULL,  -- running balance after this transaction
  description VARCHAR(255),
  stripe_session_id VARCHAR(255),  -- links to Stripe checkout for purchases
  scan_id UUID REFERENCES scans(id) ON DELETE SET NULL,  -- links to scan for consumption
  created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_client_credits_balance
  ON client_credits(client_id, credit_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_client_credits_stripe
  ON client_credits(stripe_session_id) WHERE stripe_session_id IS NOT NULL;

-- Give existing clients 50 free scan credits as welcome bonus
INSERT INTO client_credits (client_id, credit_type, amount, balance_after, description)
SELECT id, 'scan', 50, 50, 'Welcome bonus — 50 free scan credits'
FROM clients
WHERE id NOT IN (SELECT DISTINCT client_id FROM client_credits);
