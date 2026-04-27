-- Migration 016: LLM Usage Log
-- Tracks ALL LLM API calls across the platform (Anthropic, OpenAI, Gemini)
-- for superadmin cost monitoring dashboard.
--
-- Unlike scan_llm_results (which stores scan test outputs), this table logs
-- every LLM invocation: topic classification, persona generation, editorial,
-- question generation, and scan tests — giving a complete picture of API spend.

BEGIN;

CREATE TABLE IF NOT EXISTS llm_usage_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider        VARCHAR(30)  NOT NULL,       -- 'anthropic', 'openai', 'gemini'
    model           VARCHAR(100) NOT NULL,
    operation       VARCHAR(50)  NOT NULL,       -- 'classify_topics', 'generate_personas',
                                                 -- 'generate_questions', 'generate_editorial',
                                                 -- 'scan_test', 'generate_brief'
    input_tokens    INTEGER      NOT NULL DEFAULT 0,
    output_tokens   INTEGER      NOT NULL DEFAULT 0,
    cost_usd        FLOAT        NOT NULL DEFAULT 0,
    duration_ms     INTEGER,
    scan_id         UUID         REFERENCES scans(id)  ON DELETE SET NULL,
    client_id       UUID         REFERENCES clients(id) ON DELETE SET NULL,
    error           BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Admin dashboard queries: daily cost by provider, monthly totals
CREATE INDEX idx_llm_usage_provider_date ON llm_usage_log (provider, created_at DESC);
-- Per-client cost tracking
CREATE INDEX idx_llm_usage_client_date   ON llm_usage_log (client_id, created_at DESC)
    WHERE client_id IS NOT NULL;
-- Per-operation breakdown
CREATE INDEX idx_llm_usage_operation     ON llm_usage_log (operation, created_at DESC);

COMMIT;
