-- 052_reddit_per_brand_sentiment.sql
--
-- Sprint 8 audit polish round 2 - per-brand sentiment.
--
-- Background : the single `sentiment` column was ambiguous when both
-- target AND a competitor were mentioned in the same Reddit thread.
-- "Negative" could mean target getting trashed OR competitor getting
-- trashed - very different actions for the user. We need per-brand
-- sentiment to detect the real `you_lost` case (head-to-head where
-- the competitor wins).
--
-- New columns :
--   target_sentiment      : sentiment toward the user's target brand
--   competitor_sentiment  : aggregate sentiment toward any in-scope competitor
--                           (when multiple competitors are mentioned, Haiku
--                           returns a single representative judgment).
--
-- Both follow the same enum as `sentiment` :
--   positive | negative | neutral | mixed | unclear | NULL (not in scope)
--
-- The overall `sentiment` column is kept for back-compat and for
-- threads where only one side is in scope.
--
-- Extended classification values (replaces v1 set) :
--   competitor_wins  : competitor named, target absent
--   you_lost         : BOTH named, competitor sentiment positive
--                       AND target sentiment negative or neutral
--   shared_crisis    : BOTH named, BOTH sentiment negative
--   shared_win       : BOTH named, BOTH sentiment positive or neutral
--   you_win_strong   : BOTH named, target positive AND competitor negative
--   head_to_head     : BOTH named, mixed/unclear sentiment from Haiku
--   you_win          : target named, no competitor
--   neutral          : neither named

ALTER TABLE scan_reddit_threads
  ADD COLUMN IF NOT EXISTS target_sentiment TEXT,
  ADD COLUMN IF NOT EXISTS competitor_sentiment TEXT;

-- Extend the per-brand sentiment CHECK to the same enum as the global one.
ALTER TABLE scan_reddit_threads
  DROP CONSTRAINT IF EXISTS rt_target_sentiment_values;
ALTER TABLE scan_reddit_threads
  ADD CONSTRAINT rt_target_sentiment_values CHECK (
    target_sentiment IS NULL OR target_sentiment IN ('positive', 'negative', 'neutral', 'mixed', 'unclear')
  );

ALTER TABLE scan_reddit_threads
  DROP CONSTRAINT IF EXISTS rt_competitor_sentiment_values;
ALTER TABLE scan_reddit_threads
  ADD CONSTRAINT rt_competitor_sentiment_values CHECK (
    competitor_sentiment IS NULL OR competitor_sentiment IN ('positive', 'negative', 'neutral', 'mixed', 'unclear')
  );

-- Replace the classification CHECK to allow the new values. Old rows
-- will keep their previous values and be re-classified on next audit.
ALTER TABLE scan_reddit_threads
  DROP CONSTRAINT IF EXISTS rt_classification_values;
-- Note : we don't add a new CHECK constraint here because the
-- classification vocabulary may evolve further as we learn from prod
-- data. The handler enforces the enum on write. Adding a CHECK is
-- backlog Sprint 8.x once we're sure the labels are stable.
