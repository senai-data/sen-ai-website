-- Migration 003: Change FK constraints to SET NULL for persona/question deletion safety.
-- Allows deleting personas/questions without losing LLM results or opportunities.

-- scan_personas.topic_id: RESTRICT → SET NULL (deleting a topic shouldn't block)
ALTER TABLE scan_personas DROP CONSTRAINT IF EXISTS scan_personas_topic_id_fkey;
ALTER TABLE scan_personas ADD CONSTRAINT scan_personas_topic_id_fkey
  FOREIGN KEY (topic_id) REFERENCES scan_topics(id) ON DELETE SET NULL;

-- scan_llm_results.question_id: CASCADE → SET NULL (keep results when question deleted)
ALTER TABLE scan_llm_results ALTER COLUMN question_id DROP NOT NULL;
ALTER TABLE scan_llm_results DROP CONSTRAINT IF EXISTS scan_llm_results_question_id_fkey;
ALTER TABLE scan_llm_results ADD CONSTRAINT scan_llm_results_question_id_fkey
  FOREIGN KEY (question_id) REFERENCES scan_questions(id) ON DELETE SET NULL;

-- scan_opportunities.question_id: RESTRICT → SET NULL (keep opportunities when question deleted)
ALTER TABLE scan_opportunities DROP CONSTRAINT IF EXISTS scan_opportunities_question_id_fkey;
ALTER TABLE scan_opportunities ADD CONSTRAINT scan_opportunities_question_id_fkey
  FOREIGN KEY (question_id) REFERENCES scan_questions(id) ON DELETE SET NULL;
