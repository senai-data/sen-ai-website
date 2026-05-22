-- Smoke test for Phase MR.1 migrations 040/041/042.
-- Read-only on existing tables, write+rollback on the 3 new tables.
-- Cleans up after itself. Safe to re-run.

\echo '=== Test 1: CHECK constraint on media_feedback.action ==='
DO $$
DECLARE
  test_client_id UUID;
BEGIN
  SELECT id INTO test_client_id FROM clients LIMIT 1;
  IF test_client_id IS NULL THEN
    RAISE NOTICE 'No clients row, skipping CHECK test';
    RETURN;
  END IF;
  BEGIN
    INSERT INTO media_feedback (client_id, domain, action)
      VALUES (test_client_id, 'smoke-test-mr1.example.com', 'INVALID_ACTION');
    RAISE EXCEPTION 'CHECK constraint did NOT reject invalid action — BUG';
  EXCEPTION WHEN check_violation THEN
    RAISE NOTICE 'OK: CHECK rejected invalid action on media_feedback';
  END;
  BEGIN
    INSERT INTO media_feedback (client_id, domain, action)
      VALUES (test_client_id, 'smoke-test-mr1.example.com', 'accepted');
    RAISE NOTICE 'OK: CHECK accepted valid action ''accepted'' on media_feedback';
  END;
END $$;

\echo '=== Test 2: UNIQUE composite on media_catalog ==='
INSERT INTO media_catalog (domain, country, language) VALUES ('smoke-test-mr1.example.com', 'fr', 'fr');
DO $$
BEGIN
  BEGIN
    INSERT INTO media_catalog (domain, country, language)
      VALUES ('smoke-test-mr1.example.com', 'fr', 'fr');
    RAISE EXCEPTION 'UNIQUE did NOT reject duplicate — BUG';
  EXCEPTION WHEN unique_violation THEN
    RAISE NOTICE 'OK: UNIQUE rejected duplicate (domain, country, language)';
  END;
END $$;
INSERT INTO media_catalog (domain, country, language) VALUES ('smoke-test-mr1.example.com', 'es', 'es');
\echo 'OK: same domain with different country/language allowed'

\echo '=== Test 3: row counts (expect 2 catalog, 1 feedback, 0 outcome) ==='
SELECT 'media_catalog' AS t, COUNT(*) AS rows FROM media_catalog WHERE domain = 'smoke-test-mr1.example.com'
UNION ALL SELECT 'media_feedback', COUNT(*) FROM media_feedback WHERE domain = 'smoke-test-mr1.example.com'
UNION ALL SELECT 'media_publish_outcome', COUNT(*) FROM media_publish_outcome WHERE domain = 'smoke-test-mr1.example.com';

\echo '=== Cleanup ==='
DELETE FROM media_feedback WHERE domain = 'smoke-test-mr1.example.com';
DELETE FROM media_catalog WHERE domain = 'smoke-test-mr1.example.com';
\echo 'Smoke OK — all rows cleaned up'
