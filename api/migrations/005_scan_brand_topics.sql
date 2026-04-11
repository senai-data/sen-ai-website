-- Migration 005: Brand-topic junction table (Brand scoping v2).
-- Links brands to the topics they're relevant in, per scan.
-- A brand can appear in multiple topics (e.g., CeraVe competes in both "Acne" and "Moisturizers").
-- Populated by Claude during classify_topics.

CREATE TABLE IF NOT EXISTS scan_brand_topics (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scan_id UUID NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
  brand_id UUID NOT NULL REFERENCES client_brands(id) ON DELETE CASCADE,
  topic_id UUID NOT NULL REFERENCES scan_topics(id) ON DELETE CASCADE,
  created_at TIMESTAMP DEFAULT NOW(),
  UNIQUE (scan_id, brand_id, topic_id)
);

CREATE INDEX IF NOT EXISTS idx_sbt_scan ON scan_brand_topics(scan_id);
CREATE INDEX IF NOT EXISTS idx_sbt_topic ON scan_brand_topics(topic_id);
