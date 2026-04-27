-- M3: Audit log — track key user and admin actions for compliance.

CREATE TABLE audit_log (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    action VARCHAR(100) NOT NULL,
    target_type VARCHAR(50),         -- 'scan', 'client', 'user', 'credit', etc.
    target_id VARCHAR(255),          -- UUID or identifier of the target
    details JSONB DEFAULT '{}',      -- extra context (ip, user_agent, etc.)
    ip_address VARCHAR(45),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_log_user ON audit_log(user_id);
CREATE INDEX idx_audit_log_action ON audit_log(action);
CREATE INDEX idx_audit_log_created ON audit_log(created_at);
