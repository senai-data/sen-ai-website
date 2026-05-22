"""Phase MR.1 — Build & maintain `media_catalog` from existing scan data.

This module is the worker-side I/O layer behind the suggest-media feature.
It does NOT serve suggestions (that's `services/media_replacement.py`, Sprint 2).
It aggregates `scan_llm_results.citations` into a deduplicated buyable-media
table, then asks LinkFinder for prices on the domains we don't have prices for.

Architecture choice :
  LinkFinder is NOT a catalog source. It has no category API — feeding it
  arbitrary keywords like "skincare" returns nothing. The CATALOG comes from
  what the LLMs ALREADY citE in scan_llm_results (cross-tenant aggregation,
  no per-client attribution stored). LinkFinder is then queried per known
  domain to fetch DA / TF / CF / RD / price.

K-anonymity / privacy :
  media_catalog has no client_id column. Domains are aggregated raw with
  llm_citation_count + decayed score + topic_areas. The per-scan
  k-anonymity (≥3 distinct clients) filter belongs to the SERVING service
  (Sprint 2) when reading scan_llm_results directly for source-2 cross-scan
  cascade. The catalog itself is fully shared.

See [project_phase_mr1_media_catalog.md] memory + the design doc
`C:\\Users\\leed\\.claude\\plans\\cheeky-questing-lemur.md`.
"""

from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from datetime import datetime
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


# Citation rows older than this contribute (almost) nothing to the decayed
# score (0.9^36 ≈ 0.02). We still keep them as raw citation_count for debug
# but skip them in the decayed aggregator to keep the SQL bounded.
DECAY_CUTOFF_MONTHS = 36

# LLM grounding / search-engine infrastructure that leaks into citations as if
# it were a media. Universal across verticals & locales (not a brand list, so
# safe to hardcode per feedback_no_hardcoded_vertical.md). Anything we identify
# at write time is permanently kept out of the catalog. Extend as new
# grounding hosts appear.
_INFRA_HOST_DENYLIST = frozenset({
    "vertexaisearch.cloud.google.com",
    "search.googleapis.com",
    "googleusercontent.com",
    "google.com",
    "bing.com",
    "duckduckgo.com",
})

# LinkFinder bulk endpoint hard limit. From link_finder_client.BULK_BATCH_SIZE.
LINKFINDER_BATCH_SIZE = 300

# How long a prior LinkFinder check is considered fresh. Re-fetching a known
# price every day burns LinkFinder API quota for no gain.
LINKFINDER_RECHECK_DAYS = 7

# Babbar is NOT batched (each /host/overview/main call = 1 domain). Cap per
# run to keep cron time bounded. With ~1 call/s typical and 300 cap, ~5 min.
BABBAR_BATCH_SIZE = 300

# Babbar authority metrics drift slower than netlinking prices. Re-check
# monthly instead of weekly. Reduces API quota pressure significantly.
BABBAR_RECHECK_DAYS = 30

# Universal country-text → ISO-2 normalization. This is locale, NOT a
# vertical/brand list (per feedback_no_hardcoded_vertical.md, hardcoding
# verticals is forbidden but locale taxonomies are universal). Extend as
# new markets join. Unknown → returned as None and the row is SKIPPED at
# the boundary rather than polluting the catalog with an "XX" bucket.
_COUNTRY_NORMALIZE = {
    "FR": "FR", "FRANCE": "FR",
    "BE": "BE", "BELGIUM": "BE", "BELGIQUE": "BE",
    "CH": "CH", "SWITZERLAND": "CH", "SUISSE": "CH",
    "LU": "LU", "LUXEMBOURG": "LU",
    "CA": "CA", "CANADA": "CA",
    "QC": "CA",  # Quebec
    "US": "US", "USA": "US", "UNITED STATES": "US",
    "UK": "GB", "GB": "GB", "UNITED KINGDOM": "GB", "BRITAIN": "GB", "ENGLAND": "GB",
    "IE": "IE", "IRELAND": "IE",
    "DE": "DE", "GERMANY": "DE", "DEUTSCHLAND": "DE",
    "AT": "AT", "AUSTRIA": "AT",
    "ES": "ES", "SPAIN": "ES", "ESPAÑA": "ES", "ESPANA": "ES",
    "IT": "IT", "ITALY": "IT", "ITALIA": "IT",
    "PT": "PT", "PORTUGAL": "PT",
    "NL": "NL", "NETHERLANDS": "NL", "HOLLAND": "NL",
    "PL": "PL", "POLAND": "PL",
    "BR": "BR", "BRAZIL": "BR", "BRASIL": "BR",
    "MX": "MX", "MEXICO": "MX", "MÉXICO": "MX",
    "AR": "AR", "ARGENTINA": "AR",
    "AU": "AU", "AUSTRALIA": "AU",
    "NZ": "NZ", "NEW ZEALAND": "NZ",
    "JP": "JP", "JAPAN": "JP",
    "KR": "KR", "SOUTH KOREA": "KR", "KOREA": "KR",
    "CN": "CN", "CHINA": "CN",
    "IN": "IN", "INDIA": "IN",
    "SG": "SG", "SINGAPORE": "SG",
    "MA": "MA", "MOROCCO": "MA", "MAROC": "MA",
}

# Default language per ISO-2 country. When a country is bilingual we pick the
# dominant publishing language (BE → fr because most BE netlinking media is
# francophone; FR client targets francophone Belgian media). Sprint 2 can
# override per scan if needed.
_COUNTRY_TO_LANGUAGE = {
    "FR": "fr", "BE": "fr", "CH": "fr", "LU": "fr", "MC": "fr", "MA": "fr",
    "CA": "fr",
    "US": "en", "UK": "en", "GB": "en", "IE": "en", "AU": "en", "NZ": "en", "SG": "en", "IN": "en",
    "DE": "de", "AT": "de",
    "ES": "es", "MX": "es", "AR": "es",
    "IT": "it",
    "PT": "pt", "BR": "pt",
    "NL": "nl",
    "PL": "pl",
    "JP": "ja",
    "KR": "ko",
    "CN": "zh",
}


def normalize_domain(raw: str | None) -> str:
    """Strip protocol, www., trailing path. Return lowercase bare domain or ''.

    Mirrors `worker/services/media_picker.py:_normalize_domain` so the two
    code paths see the same key for the same citation row.
    """
    if not raw:
        return ""
    s = str(raw).strip().lower()
    s = re.sub(r"^https?://", "", s)
    if s.startswith("www."):
        s = s[4:]
    s = s.split("/", 1)[0].rstrip(".")
    return s if "." in s else ""


def normalize_country(raw: str | None) -> str | None:
    """Free-form country text (scan.config.domain_brief.country) → ISO-2 or None.

    Returns None when the input doesn't match any known market. Caller MUST
    skip the row in that case rather than fall back to a default — the
    (domain, country, language) PK depends on country being trustworthy.
    Strips trailing parenthetical disambiguation, e.g.
    "France (with operations in...)" → "France" → "FR".
    """
    if not raw:
        return None
    s = str(raw).strip()
    # Drop "France (with ops in X, Y)" → "France"
    s = re.split(r"[(,;]", s, maxsplit=1)[0].strip()
    return _COUNTRY_NORMALIZE.get(s.upper())


def country_to_language(country_code: str | None) -> str | None:
    """ISO-2 country → ISO-639-1 language. None on unknown."""
    if not country_code:
        return None
    return _COUNTRY_TO_LANGUAGE.get(country_code.upper())


def _months_old(ts: datetime, now: datetime | None = None) -> float:
    now = now or datetime.utcnow()
    delta = now - ts
    return max(0.0, delta.total_seconds() / (30.44 * 86400))


def _decay_weight(months_old: float) -> float:
    """0.9^months_old. Caps at DECAY_CUTOFF_MONTHS to keep the sum bounded."""
    return 0.9 ** min(months_old, DECAY_CUTOFF_MONTHS)


def collect_filtered_domains(db: Session) -> set[str]:
    """Domains we NEVER want in media_catalog : every client_brand domain
    (own brand of SOME client) plus every scan-classified competitor domain.

    Cross-tenant union — we err on the side of broad exclusion so a domain
    that is "my_brand" for client A and "competitor" for client B is dropped
    everywhere. The cost is losing a few legitimate media (rare), the
    benefit is no accidental brand-self-citation in the catalog.
    """
    out: set[str] = set()
    rows = db.execute(text("""
        SELECT DISTINCT lower(domain)
          FROM client_brands
         WHERE domain IS NOT NULL AND domain <> ''
    """)).fetchall()
    for (d,) in rows:
        nd = normalize_domain(d)
        if nd:
            out.add(nd)
    rows = db.execute(text("""
        SELECT DISTINCT lower(cb.domain)
          FROM scan_brand_classifications sbc
          JOIN client_brands cb ON cb.id = sbc.brand_id
         WHERE sbc.classification = 'competitor'
           AND cb.domain IS NOT NULL AND cb.domain <> ''
    """)).fetchall()
    for (d,) in rows:
        nd = normalize_domain(d)
        if nd:
            out.add(nd)
    return out


def aggregate_citations(
    db: Session,
    *,
    excluded_domains: set[str] | None = None,
    now: datetime | None = None,
) -> dict[tuple[str, str, str], dict]:
    """Walk every scan_llm_results.citations row and aggregate into
    {(domain, country, language): {...}} buckets.

    Filters out :
      - est_site_cible=True (the scanned site itself)
      - is_pr_source=True (sponsored / press-release — not editorial)
      - empty domains, single-token (no dot)
      - any domain in `excluded_domains` (own-brand + competitor union)
      - scans whose country is not in the normalized mapping (skip silently)

    Inferred per bucket :
      - llm_citation_count  : raw count of qualifying citation rows
      - llm_citation_decayed: Σ 0.9^months_old per citation (capped 36mo)
      - llm_citation_last_seen : max created_at across citations
      - vertical[] : dedup'd raw industry strings from scan.config.domain_brief.industry
      - topic_areas[] : top-3 most-cited ScanTopic.name from joined topics

    Uses a single streaming SQL query joined with the topic taxonomy. Memory
    is O(unique-buckets) — typically a few hundred rows even on a large
    corpus, so plain dict is fine.
    """
    excluded = excluded_domains or set()
    now = now or datetime.utcnow()

    # Stream the citation array out of JSONB via jsonb_array_elements. Joining
    # the topic taxonomy through persona→topic gives us topic_areas per
    # citation. We rely on the SQL planner to push the citation expansion
    # down; for 1k-2k ScanLLMResult rows with ~5 citations each this is
    # well under a second.
    rows = db.execute(text("""
        SELECT
            slr.created_at,
            slr.provider,
            citation,
            s.config,
            t.name AS topic_name
          FROM scan_llm_results slr
          JOIN scans s ON s.id = slr.scan_id
          LEFT JOIN scan_questions sq ON sq.id = slr.question_id
          LEFT JOIN scan_personas sp ON sp.id = sq.persona_id
          LEFT JOIN scan_topics t ON t.id = sp.topic_id
          CROSS JOIN LATERAL jsonb_array_elements(slr.citations) AS citation
         WHERE jsonb_typeof(slr.citations) = 'array'
    """)).fetchall()

    buckets: dict[tuple[str, str, str], dict] = {}
    topic_counts: dict[tuple[str, str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for created_at, provider, citation, scan_config, topic_name in rows:
        if not isinstance(citation, dict):
            continue
        if citation.get("est_site_cible"):
            continue
        if citation.get("is_pr_source"):
            continue
        domain = normalize_domain(citation.get("domaine") or citation.get("domain"))
        if not domain or domain in excluded:
            continue
        if domain in _INFRA_HOST_DENYLIST:
            continue

        raw_country = ((scan_config or {}).get("domain_brief") or {}).get("country")
        country = normalize_country(raw_country)
        language = country_to_language(country)
        if not country or not language:
            continue  # unmapped locale — skip silently per design

        industry = (((scan_config or {}).get("domain_brief") or {}).get("industry") or "").strip()

        key = (domain, country, language)
        bucket = buckets.get(key)
        if bucket is None:
            bucket = {
                "domain": domain,
                "country": country,
                "language": language,
                "llm_citation_count": 0,
                "llm_citation_decayed": 0.0,
                "llm_citation_last_seen": created_at,
                "providers": set(),
                "verticals": set(),
            }
            buckets[key] = bucket

        bucket["llm_citation_count"] += 1
        bucket["llm_citation_decayed"] += _decay_weight(_months_old(created_at, now))
        if created_at and (bucket["llm_citation_last_seen"] is None or created_at > bucket["llm_citation_last_seen"]):
            bucket["llm_citation_last_seen"] = created_at
        if provider:
            bucket["providers"].add(provider)
        if industry:
            bucket["verticals"].add(industry)
        if topic_name:
            topic_counts[key][topic_name] += 1

    # Materialize topic_areas (top-3 by citation count) + freeze sets to lists.
    for key, bucket in buckets.items():
        topics_for_key = topic_counts.get(key, {})
        top3 = sorted(topics_for_key.items(), key=lambda kv: kv[1], reverse=True)[:3]
        bucket["topic_areas"] = [name for name, _ in top3]
        bucket["vertical"] = sorted(bucket["verticals"])
        bucket["providers_list"] = sorted(bucket["providers"])
        # Drop the set objects — not JSON-friendly downstream.
        bucket.pop("verticals", None)
        bucket.pop("providers", None)

    return buckets


def upsert_catalog_rows(
    db: Session,
    buckets: dict[tuple[str, str, str], dict],
) -> tuple[int, int]:
    """UPSERT each bucket into media_catalog. Returns (inserted, updated).

    Counters are REPLACED, not incremented (we always re-aggregate from the
    full source). vertical[] and topic_areas[] are also replaced — they're
    derived from the current snapshot of scan data, not append-only.

    Preserves the LinkFinder-enriched fields (price_eur, da, tf, cf, rd,
    media_group, editorial_voice, audience_tags, site_type, reputation_flags,
    linkfinder_last_check) when the row already exists. That's the whole
    point of the upsert — discovery refreshes citation stats, enrichment
    refreshes the rest, and the two don't stomp on each other.
    """
    if not buckets:
        return (0, 0)

    inserted = 0
    updated = 0
    for bucket in buckets.values():
        result = db.execute(text("""
            INSERT INTO media_catalog (
                domain, country, language,
                vertical, topic_areas,
                llm_citation_count, llm_citation_decayed, llm_citation_last_seen,
                updated_at
            ) VALUES (
                :domain, :country, :language,
                CAST(:vertical AS text[]), CAST(:topic_areas AS text[]),
                :count, :decayed, :last_seen,
                NOW()
            )
            ON CONFLICT (domain, country, language) DO UPDATE SET
                vertical = EXCLUDED.vertical,
                topic_areas = EXCLUDED.topic_areas,
                llm_citation_count = EXCLUDED.llm_citation_count,
                llm_citation_decayed = EXCLUDED.llm_citation_decayed,
                llm_citation_last_seen = EXCLUDED.llm_citation_last_seen,
                updated_at = NOW()
            RETURNING (xmax = 0) AS inserted
        """), {
            "domain": bucket["domain"],
            "country": bucket["country"],
            "language": bucket["language"],
            "vertical": bucket["vertical"],
            "topic_areas": bucket["topic_areas"],
            "count": bucket["llm_citation_count"],
            "decayed": round(float(bucket["llm_citation_decayed"]), 3),
            "last_seen": bucket["llm_citation_last_seen"],
        })
        row = result.fetchone()
        if row and row[0]:
            inserted += 1
        else:
            updated += 1
    db.commit()
    return (inserted, updated)


def enrich_with_linkfinder(
    db: Session,
    *,
    recheck_days: int = LINKFINDER_RECHECK_DAYS,
    max_domains: int | None = None,
) -> dict:
    """Fetch price_eur (+ platform_url) from LinkFinder for catalog rows
    whose linkfinder_last_check is stale. Caps at LINKFINDER_BATCH_SIZE.

    LinkFinder owns ONLY price data since Phase MR.1.5 (2026-05-21). Authority
    metrics (da/tf/cf/rd) come from Babbar via `enrich_with_babbar()` —
    LinkFinder returns DA/TF/CF/RD unreliably (0 rows with DA observed on
    first prod run despite 53 priced rows).

    A row gets `linkfinder_last_check = NOW()` even when LinkFinder returns
    `source='not_found'` — that's the "we asked, no price available" memo so
    we don't re-ask tomorrow. price_eur stays NULL ; the suggest-media
    service decides whether to surface it (cas A allows null, cas B
    requires price > 0).
    """
    cutoff = text("""
        SELECT domain, country, language FROM media_catalog
         WHERE (linkfinder_last_check IS NULL
                OR linkfinder_last_check < NOW() - (:days || ' days')::interval)
         ORDER BY llm_citation_decayed DESC
         LIMIT :lim
    """)
    limit = max_domains if max_domains else LINKFINDER_BATCH_SIZE
    candidates = db.execute(cutoff, {"days": recheck_days, "lim": limit}).fetchall()
    if not candidates:
        return {"checked": 0, "enriched": 0, "not_found": 0}

    domains = [c[0] for c in candidates]
    keys_by_domain = {c[0]: (c[0], c[1], c[2]) for c in candidates}

    try:
        # seo_llm submodule layout : worker/seo_llm/src/link_finder_client.py
        from seo_llm.src.link_finder_client import LinkFinderClient
    except ImportError:
        logger.exception("LinkFinder client import failed — skipping enrichment")
        return {"checked": 0, "enriched": 0, "not_found": 0, "error": "import_failed"}

    client = LinkFinderClient()
    if not client.is_api_configured:
        logger.warning(
            "LinkFinder not configured (LINKFINDER_EMAIL/PASSWORD/SESSION_COOKIE) — "
            "skipping enrichment, will retry next sweep"
        )
        return {"checked": 0, "enriched": 0, "not_found": 0, "error": "no_credentials"}

    try:
        results = client.get_prices_batch(domains)
    except Exception:
        logger.exception("LinkFinder.get_prices_batch crashed — skipping enrichment")
        return {"checked": 0, "enriched": 0, "not_found": 0, "error": "linkfinder_crashed"}

    enriched = 0
    not_found = 0
    now = datetime.utcnow()
    for domain, info in results.items():
        key = keys_by_domain.get(domain)
        if not key:
            continue
        domain_n, country_n, language_n = key
        source = (info or {}).get("source")
        if source == "not_found":
            not_found += 1
            db.execute(text("""
                UPDATE media_catalog
                   SET linkfinder_last_check = :now,
                       updated_at = NOW()
                 WHERE domain = :d AND country = :c AND language = :l
            """), {"now": now, "d": domain_n, "c": country_n, "l": language_n})
            continue
        # MR.1.5 : only price (+ platform_url for future Sprint 2 UX).
        # da/tf/cf/rd intentionally left to enrich_with_babbar — LinkFinder's
        # authority columns are unreliable in our tier.
        db.execute(text("""
            UPDATE media_catalog
               SET price_eur = :price,
                   linkfinder_last_check = :now,
                   updated_at = NOW()
             WHERE domain = :d AND country = :c AND language = :l
        """), {
            "price": info.get("prix_ht"),
            "now": now,
            "d": domain_n, "c": country_n, "l": language_n,
        })
        enriched += 1
    db.commit()

    return {
        "checked": len(candidates),
        "enriched": enriched,
        "not_found": not_found,
    }


def enrich_with_babbar(
    db: Session,
    *,
    recheck_days: int = BABBAR_RECHECK_DAYS,
    max_domains: int | None = None,
) -> dict:
    """Fetch authority metrics (da/tf/cf/rd) from Babbar for catalog rows
    whose babbar_last_check is stale. Caps at BABBAR_BATCH_SIZE.

    Babbar field → our column mapping :
      - hostTrust (=babbarAuthorityScore / BAS) → da
      - domainTrust                             → tf
      - semanticValue                           → cf
      - backlinksCount                          → rd

    A row gets `babbar_last_check = NOW()` even when Babbar returns no
    metrics (domain not in their index) — same idempotency memo as the
    LinkFinder path. da/tf/cf/rd stay NULL ; scoring at suggest time
    must tolerate NULL (Sprint 2 ranking treats NULL → 0).

    Cost : each domain = 1 Babbar API call (no native batch). With ~300
    domain cap and ~1 call/s typical, ~5 min wall time per run. Rate
    limiting is enforced inside `BabbarClient._wait_if_needed()` via the
    x-ratelimit-remaining response header.
    """
    candidates = db.execute(text("""
        SELECT domain, country, language FROM media_catalog
         WHERE (babbar_last_check IS NULL
                OR babbar_last_check < NOW() - (:days || ' days')::interval)
         ORDER BY llm_citation_decayed DESC
         LIMIT :lim
    """), {"days": recheck_days, "lim": max_domains or BABBAR_BATCH_SIZE}).fetchall()
    if not candidates:
        return {"checked": 0, "enriched": 0, "not_found": 0}

    try:
        from seo_llm.src.babbar_client import BabbarClient
    except ImportError:
        logger.exception("Babbar client import failed — skipping authority enrichment")
        return {"checked": 0, "enriched": 0, "not_found": 0, "error": "import_failed"}

    client = BabbarClient()
    if not client.api_key:
        logger.warning(
            "BABBAR_API_KEY not configured — skipping authority enrichment, "
            "will retry next sweep"
        )
        return {"checked": 0, "enriched": 0, "not_found": 0, "error": "no_credentials"}

    enriched = 0
    not_found = 0
    now = datetime.utcnow()
    for domain, country, language in candidates:
        try:
            metrics = client.get_domain_metrics_cached(domain)
        except Exception:
            logger.exception(f"Babbar lookup crashed for {domain} — marking checked, leaving NULL")
            metrics = None

        if not metrics or metrics.get("domainTrust") is None:
            not_found += 1
            try:
                db.execute(text("""
                    UPDATE media_catalog
                       SET babbar_last_check = :now,
                           updated_at = NOW()
                     WHERE domain = :d AND country = :c AND language = :l
                """), {"now": now, "d": domain, "c": country, "l": language})
                db.commit()
            except Exception:
                db.rollback()
            continue

        # Map Babbar fields → our column names. See migration 043 comments.
        da_val = metrics.get("hostTrust")
        tf_val = metrics.get("domainTrust")
        cf_val = metrics.get("semanticValue")
        rd_val = metrics.get("backlinksCount")
        # Coerce to int where Postgres expects integers (Babbar may return float).
        def _as_int(v):
            return int(v) if isinstance(v, (int, float)) else None

        # Per-row commit + try/except : a single bad value (e.g. a backlinks
        # count that overflows, or any constraint hiccup) must NOT roll back
        # the entire batch — that bug stalled authority coverage at 5 rows.
        try:
            db.execute(text("""
                UPDATE media_catalog
                   SET da = :da, tf = :tf, cf = :cf, rd = :rd,
                       babbar_last_check = :now,
                       updated_at = NOW()
                 WHERE domain = :d AND country = :c AND language = :l
            """), {
                "da": _as_int(da_val),
                "tf": _as_int(tf_val),
                "cf": _as_int(cf_val),
                "rd": _as_int(rd_val),
                "now": now,
                "d": domain, "c": country, "l": language,
            })
            db.commit()
            enriched += 1
        except Exception:
            db.rollback()
            logger.exception(
                f"Babbar enrich failed for {domain} "
                f"(da={da_val} tf={tf_val} cf={cf_val} rd={rd_val}) — "
                f"stamping checked, skipping"
            )
            # Stamp babbar_last_check anyway so we don't retry this bad row
            # every night. da/tf/cf/rd stay whatever they were (likely NULL).
            try:
                db.execute(text("""
                    UPDATE media_catalog SET babbar_last_check = :now, updated_at = NOW()
                     WHERE domain = :d AND country = :c AND language = :l
                """), {"now": now, "d": domain, "c": country, "l": language})
                db.commit()
            except Exception:
                db.rollback()

    return {
        "checked": len(candidates),
        "enriched": enriched,
        "not_found": not_found,
    }
