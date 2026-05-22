"""Handler: rebuild & enrich the cross-tenant media_catalog table.

Reads :
- scan_llm_results.citations (every row, every scan, every client)
- scans.config.domain_brief.country + .industry (per-scan locale + vertical)
- scan_questions → scan_personas → scan_topics (for topic_areas inference)
- client_brands.domain + scan_brand_classifications (for exclusion list)

Writes :
- UPSERT into media_catalog (citation stats columns)
- UPDATE media_catalog (LinkFinder enrichment columns, batched)

Triggered :
- Cron (nightly) from worker/main.py:enqueue_media_catalog_discovery — Jalon 3
- Manually via Job(job_type='discover_media_catalog', payload={}) for one-shot
  bootstrap or after a big citation ingest.

Idempotent — counters are REPLACED (full re-aggregation), enrichment is
throttled per-domain to LINKFINDER_RECHECK_DAYS (default 7).

Cost : Zero LLM tokens (pure SQL aggregation). Optional LinkFinder API call
(1 bulk request ≤ 300 domains, ~3 s wall time when their endpoint is healthy).

Job payload :
    {
      "enrich_authority": bool,        # default True. False = skip Babbar.
      "enrich_price": bool,            # default True. False = skip LinkFinder.
      "max_enrich_babbar": int,        # default BABBAR_BATCH_SIZE.
      "max_enrich_linkfinder": int,    # default LINKFINDER_BATCH_SIZE.
      "babbar_recheck_days": int,      # default BABBAR_RECHECK_DAYS (30).
      "linkfinder_recheck_days": int,  # default LINKFINDER_RECHECK_DAYS (7).

      # Backwards-compat aliases (deprecated, will be removed Sprint 2) :
      "enrich": bool,         # alias for {authority, price} both
      "max_enrich": int,      # alias for both batch caps
      "recheck_days": int,    # alias for both recheck thresholds
    }

PARITÉ : no models.py touch — uses raw SQL via SQLAlchemy text() so we don't
need the new ORM classes to be loaded before the migration is in.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy.orm import Session

from services.media_catalog_io import (
    BABBAR_BATCH_SIZE,
    BABBAR_RECHECK_DAYS,
    LINKFINDER_BATCH_SIZE,
    LINKFINDER_RECHECK_DAYS,
    aggregate_citations,
    collect_filtered_domains,
    enrich_with_babbar,
    enrich_with_linkfinder,
    upsert_catalog_rows,
)

logger = logging.getLogger(__name__)


def execute(job_payload: dict, scan_id: str | None, db: Session) -> dict:
    """Rebuild media_catalog from current citations + refresh LinkFinder data.

    scan_id : unused (workspace-wide sweep). The runner passes it because
    the handler signature is fixed, but a None value is the expected case.
    """
    # Backwards-compat : flat "enrich"/"max_enrich"/"recheck_days" payload
    # used by Sprint 1 (pre-Babbar). Treat as applied to BOTH enrichers.
    legacy_enrich = job_payload.get("enrich")
    do_authority = bool(job_payload.get("enrich_authority",
                                        legacy_enrich if legacy_enrich is not None else True))
    do_price = bool(job_payload.get("enrich_price",
                                    legacy_enrich if legacy_enrich is not None else True))

    legacy_max = job_payload.get("max_enrich")
    max_babbar = int(job_payload.get("max_enrich_babbar") or legacy_max or BABBAR_BATCH_SIZE)
    max_linkfinder = int(job_payload.get("max_enrich_linkfinder") or legacy_max or LINKFINDER_BATCH_SIZE)

    legacy_recheck = job_payload.get("recheck_days")
    babbar_days = int(job_payload.get("babbar_recheck_days") or legacy_recheck or BABBAR_RECHECK_DAYS)
    linkfinder_days = int(job_payload.get("linkfinder_recheck_days") or legacy_recheck or LINKFINDER_RECHECK_DAYS)

    t0 = time.time()

    # Step 1 : build the exclusion set (own brands + competitors, cross-tenant)
    excluded = collect_filtered_domains(db)
    logger.info(f"discover_media_catalog: {len(excluded)} domains excluded (brands+competitors)")

    # Step 2 : aggregate citations into (domain, country, language) buckets
    buckets = aggregate_citations(db, excluded_domains=excluded)
    logger.info(f"discover_media_catalog: aggregated {len(buckets)} distinct (domain, country, language) buckets")

    # Step 3 : UPSERT into media_catalog
    inserted, updated = upsert_catalog_rows(db, buckets)
    logger.info(f"discover_media_catalog: upserted (inserted={inserted}, updated={updated})")

    # Step 4a : Babbar authority enrichment — fills da/tf/cf/rd
    babbar_stats: dict = {"skipped": True}
    if do_authority:
        babbar_stats = enrich_with_babbar(
            db,
            recheck_days=babbar_days,
            max_domains=max_babbar,
        )
        logger.info(f"discover_media_catalog: babbar enrichment {babbar_stats}")

    # Step 4b : LinkFinder price enrichment — fills price_eur only
    linkfinder_stats: dict = {"skipped": True}
    if do_price:
        linkfinder_stats = enrich_with_linkfinder(
            db,
            recheck_days=linkfinder_days,
            max_domains=max_linkfinder,
        )
        logger.info(f"discover_media_catalog: linkfinder enrichment {linkfinder_stats}")

    elapsed = round(time.time() - t0, 1)
    result = {
        "status": "ok",
        "buckets": len(buckets),
        "excluded_domains": len(excluded),
        "inserted": inserted,
        "updated": updated,
        "babbar": babbar_stats,
        "linkfinder": linkfinder_stats,
        "elapsed_sec": elapsed,
    }
    logger.info(f"discover_media_catalog done in {elapsed}s: {result}")
    return result
