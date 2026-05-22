-- Dry-run of the aggregation logic in worker/services/media_catalog_io.py.
-- Read-only. Validates that the JOIN + JSONB expansion would produce the
-- expected buckets WITHOUT writing to media_catalog.

\echo '=== A. Volume of citations across the corpus ==='
SELECT
    COUNT(*) AS total_llm_rows_with_citations,
    SUM(jsonb_array_length(citations)) AS total_citation_objects
  FROM scan_llm_results
 WHERE jsonb_typeof(citations) = 'array';

\echo '=== B. Exclusion set : client_brands + competitor classifications ==='
WITH excluded AS (
    SELECT DISTINCT lower(domain) AS d
      FROM client_brands
     WHERE domain IS NOT NULL AND domain <> ''
    UNION
    SELECT DISTINCT lower(cb.domain)
      FROM scan_brand_classifications sbc
      JOIN client_brands cb ON cb.id = sbc.brand_id
     WHERE sbc.classification = 'competitor'
       AND cb.domain IS NOT NULL AND cb.domain <> ''
)
SELECT COUNT(*) AS excluded_domain_count FROM excluded;

\echo '=== C. Country normalization coverage on scans ==='
SELECT
    config->'domain_brief'->>'country' AS raw_country,
    COUNT(*) AS scans
  FROM scans
 WHERE config IS NOT NULL
 GROUP BY raw_country
 ORDER BY scans DESC
 LIMIT 15;

\echo '=== D. Aggregation dry-run : buckets per (domain, country) ==='
-- Country normalized to ISO-2 inline. Subset of the mapping (top markets).
-- This must mirror media_catalog_io._COUNTRY_NORMALIZE.
WITH country_norm AS (
    SELECT * FROM (VALUES
        ('FR','FR'), ('FRANCE','FR'),
        ('BE','BE'), ('BELGIUM','BE'), ('BELGIQUE','BE'),
        ('CH','CH'), ('SWITZERLAND','CH'), ('SUISSE','CH'),
        ('CA','CA'), ('CANADA','CA'),
        ('US','US'), ('USA','US'), ('UNITED STATES','US'),
        ('UK','GB'), ('GB','GB'), ('UNITED KINGDOM','GB'),
        ('DE','DE'), ('GERMANY','DE'),
        ('ES','ES'), ('SPAIN','ES'),
        ('IT','IT'), ('ITALY','IT'),
        ('NL','NL'), ('NETHERLANDS','NL'),
        ('PT','PT'), ('PORTUGAL','PT'),
        ('BR','BR'), ('BRAZIL','BR'),
        ('AU','AU'), ('AUSTRALIA','AU')
    ) AS m(raw, iso2)
),
excluded AS (
    SELECT DISTINCT lower(domain) AS d FROM client_brands WHERE domain IS NOT NULL
    UNION
    SELECT DISTINCT lower(cb.domain) FROM scan_brand_classifications sbc
        JOIN client_brands cb ON cb.id = sbc.brand_id
        WHERE sbc.classification = 'competitor' AND cb.domain IS NOT NULL
),
expanded AS (
    SELECT
        slr.created_at,
        slr.provider,
        regexp_replace(
            lower(coalesce(citation->>'domaine', citation->>'domain', '')),
            '^www\.', ''
        ) AS domain,
        upper(split_part(regexp_replace(
            coalesce(s.config->'domain_brief'->>'country', ''),
            '\s*[(,;].*$', ''
        ), ' ', 1)) AS raw_country_token,
        (s.config->'domain_brief'->>'industry') AS industry,
        (citation->>'est_site_cible')::boolean AS is_target,
        (citation->>'is_pr_source')::boolean AS is_pr
      FROM scan_llm_results slr
      JOIN scans s ON s.id = slr.scan_id
      CROSS JOIN LATERAL jsonb_array_elements(slr.citations) AS citation
     WHERE jsonb_typeof(slr.citations) = 'array'
),
normalized AS (
    SELECT
        e.domain,
        cn.iso2 AS country,
        e.industry,
        e.created_at
      FROM expanded e
      JOIN country_norm cn ON cn.raw = e.raw_country_token
     WHERE e.domain ~ '\.'
       AND e.domain NOT IN (SELECT d FROM excluded)
       AND COALESCE(e.is_target, false) = false
       AND COALESCE(e.is_pr, false) = false
)
SELECT
    country,
    COUNT(DISTINCT domain) AS distinct_domains,
    COUNT(*) AS total_citations
  FROM normalized
 GROUP BY country
 ORDER BY total_citations DESC;

\echo '=== E. Top-20 domains across all countries (preview of catalog) ==='
WITH country_norm AS (
    SELECT * FROM (VALUES
        ('FR','FR'), ('FRANCE','FR'),
        ('BE','BE'), ('BELGIUM','BE'),
        ('CH','CH'), ('SWITZERLAND','CH'),
        ('CA','CA'), ('CANADA','CA'),
        ('US','US'), ('USA','US'), ('UNITED STATES','US'),
        ('UK','GB'), ('GB','GB'), ('UNITED KINGDOM','GB'),
        ('DE','DE'), ('GERMANY','DE'),
        ('ES','ES'), ('SPAIN','ES'),
        ('IT','IT'), ('NL','NL'),
        ('PT','PT'), ('BR','BR'), ('AU','AU')
    ) AS m(raw, iso2)
),
excluded AS (
    SELECT DISTINCT lower(domain) AS d FROM client_brands WHERE domain IS NOT NULL
    UNION
    SELECT DISTINCT lower(cb.domain) FROM scan_brand_classifications sbc
        JOIN client_brands cb ON cb.id = sbc.brand_id
        WHERE sbc.classification = 'competitor' AND cb.domain IS NOT NULL
),
expanded AS (
    SELECT
        regexp_replace(
            lower(coalesce(citation->>'domaine', citation->>'domain', '')),
            '^www\.', ''
        ) AS domain,
        upper(split_part(regexp_replace(
            coalesce(s.config->'domain_brief'->>'country', ''),
            '\s*[(,;].*$', ''
        ), ' ', 1)) AS raw_country_token,
        (citation->>'est_site_cible')::boolean AS is_target,
        (citation->>'is_pr_source')::boolean AS is_pr,
        slr.created_at
      FROM scan_llm_results slr
      JOIN scans s ON s.id = slr.scan_id
      CROSS JOIN LATERAL jsonb_array_elements(slr.citations) AS citation
     WHERE jsonb_typeof(slr.citations) = 'array'
)
SELECT
    cn.iso2 AS country,
    e.domain,
    COUNT(*) AS citations,
    MAX(e.created_at)::date AS last_seen
  FROM expanded e
  JOIN country_norm cn ON cn.raw = e.raw_country_token
 WHERE e.domain ~ '\.'
   AND e.domain NOT IN (SELECT d FROM excluded)
   AND COALESCE(e.is_target, false) = false
   AND COALESCE(e.is_pr, false) = false
 GROUP BY cn.iso2, e.domain
 ORDER BY citations DESC
 LIMIT 20;
