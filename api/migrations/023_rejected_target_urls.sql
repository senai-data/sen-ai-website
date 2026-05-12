-- 023_rejected_target_urls.sql
--
-- Track URLs the user has explicitly rejected from auto-suggest. When the user
-- clicks "Find a different page" on a content item, we add the current
-- target_url to this list and re-run FAQPageMatcher with the list as an
-- exclusion filter. Subsequent retries keep accumulating rejected URLs so the
-- matcher never returns the same page twice.
--
-- JSONB array of strings (URLs). Empty by default. Lives on the item, not the
-- scan — different items can reject different pages even if their questions
-- end up funneling toward the same candidate set.
--
-- This is the user_input side of the Pilier 3 stepping-stone (see
-- project_roadmap_content_port.md) : auto_suggest + user_override = the
-- audit signal Phase D sitemap index can fold into the confidence-scored
-- semantic search later.

ALTER TABLE scan_content_items
    ADD COLUMN IF NOT EXISTS rejected_target_urls JSONB
    NOT NULL DEFAULT '[]'::jsonb;

COMMENT ON COLUMN scan_content_items.rejected_target_urls IS
    'User-rejected target URLs accumulated across "Find a different page" clicks. '
    'FAQPageMatcher reruns skip these URLs to avoid suggesting the same page twice.';
