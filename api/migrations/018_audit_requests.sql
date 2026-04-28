-- 018: audit_requests — public audit-gratuit submissions before account creation.
--
-- Decoupled from User/Client by design: a prospect submits the form on the
-- homepage, gets a magic-link in their inbox, clicks it to confirm. Admin
-- reviews + launches the scan manually (Phase 1). Account is created
-- silently when results are delivered (Phase 1.5+).
--
-- Lifecycle:
--   pending   → just submitted, awaiting email confirmation
--   confirmed → user clicked the magic-link, admin notified
--   launched  → admin has launched the scan, scan_id populated
--   completed → results delivered to prospect
--   rejected  → admin rejected (spam, irrelevant, etc.)

CREATE TYPE audit_request_status AS ENUM (
    'pending', 'confirmed', 'launched', 'completed', 'rejected'
);

CREATE TABLE audit_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    website VARCHAR(500) NOT NULL,
    email VARCHAR(255) NOT NULL,
    topic_focus VARCHAR(500) NOT NULL,
    first_name VARCHAR(100),
    message TEXT,
    status audit_request_status NOT NULL DEFAULT 'pending',
    confirmation_jti VARCHAR(64),
    scan_id UUID REFERENCES scans(id) ON DELETE SET NULL,
    source_ip VARCHAR(45),
    user_agent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confirmed_at TIMESTAMPTZ,
    processed_at TIMESTAMPTZ
);

CREATE INDEX idx_audit_requests_status ON audit_requests(status);
CREATE INDEX idx_audit_requests_email ON audit_requests(email);
CREATE INDEX idx_audit_requests_created ON audit_requests(created_at DESC);
