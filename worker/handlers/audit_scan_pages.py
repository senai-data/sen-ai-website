"""Handler : Sprint 5 Princeton GEO content audit.

Source of URLs to audit : the user's own pages cited by the LLMs during the
scan. Drawn from scan_llm_results.citations[] where est_site_cible=true. This
is the actionable set : these are the pages LLMs already retrieve when
answering brand questions, so optimizing them moves the visibility needle
fastest.

For each URL we :
  1. Fetch the page HTML via adapters/page_fetcher
  2. Run the 7-pattern Princeton heuristics via adapters/geo_pattern_analyzer
  3. Upsert the result in scan_page_audits (one row per scan_id + url)

Idempotent : re-running the handler updates existing rows in place. The
"force" flag isn't needed - the data is cheap to recompute and the user
explicitly clicks "Refresh".

Cost : zero LLM. Plain HTTP + Python heuristics. ~1-2 s per page over
network. Bounded by a polite throttle so we don't hammer a brand's own
servers when auditing 300+ URLs in a single Ducray scan.
"""
from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from adapters.geo_pattern_analyzer import analyze_page
from adapters.page_fetcher import fetch_page

logger = logging.getLogger(__name__)

# Polite throttle between page fetches against the same host. The user's own
# site shouldn't notice 300 hits in 5 min, but better safe than caching-CDN-
# blocked.
PAGE_DELAY_SECONDS = 0.4

# Skip URLs after this many in a single run. A user can re-trigger to keep
# going past the cap.
MAX_URLS_PER_RUN = 400

# Minimum LLM citation count to bother auditing a URL. Filters out one-off
# citation noise (a URL cited once across the scan probably isn't a core
# page to optimize).
MIN_CITATION_COUNT = 1


def _cited_urls(db: Session, scan_id: str) -> list[tuple[str, int]]:
    """Return [(url, citation_count), ...] for URLs of the user's own site
    that were cited at least MIN_CITATION_COUNT times in this scan.

    Uses a single SQL pass over the JSONB citations array. Each citation is
    counted once per ScanLLMResult row - so a URL referenced 3 times in a
    single response counts as 1, but referenced once in 10 different
    question responses counts as 10. That semantic matches what we surface
    in the "Your Pages Cited" view on the Citations tab.
    """
    from sqlalchemy import text as _text

    sql = _text(
        """
        SELECT citation->>'url' AS url,
               COUNT(DISTINCT (slr.question_id, slr.provider))::int AS n
          FROM scan_llm_results slr,
               LATERAL jsonb_array_elements(slr.citations) AS citation
         WHERE slr.scan_id = :scan_id
           AND (citation->>'est_site_cible')::bool = true
           AND citation->>'url' IS NOT NULL
         GROUP BY citation->>'url'
        HAVING COUNT(DISTINCT (slr.question_id, slr.provider)) >= :min_cnt
         ORDER BY n DESC
         LIMIT :lim
        """
    )
    rows = db.execute(
        sql,
        {"scan_id": scan_id, "min_cnt": MIN_CITATION_COUNT, "lim": MAX_URLS_PER_RUN},
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def execute(job_payload: dict, scan_id: str, db: Session) -> dict:
    """Audit the cited pages of a scan against the 7 GEO patterns.

    job_payload :
      - limit (int) : cap the number of URLs audited (default MAX_URLS_PER_RUN)
      - reset (bool) : drop the existing audit rows for this scan before re-running
    """
    from models import Scan, ScanPageAudit

    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan:
        raise RuntimeError("Scan not found")

    limit = int(job_payload.get("limit") or MAX_URLS_PER_RUN)
    reset = bool(job_payload.get("reset"))

    if reset:
        db.query(ScanPageAudit).filter(ScanPageAudit.scan_id == scan_id).delete()
        db.commit()

    pairs = _cited_urls(db, scan_id)[:limit]
    if not pairs:
        logger.info(f"audit_scan_pages: no cited URLs for scan {scan_id}")
        return {"audited": 0, "errors": 0, "skipped": 0, "total": 0}

    # Determine the brand's primary domain (used by the analyzer to classify
    # internal vs external links). Fall back to the scan's domain.
    page_domain = scan.domain or ""
    try:
        page_domain = urlparse(page_domain if "://" in page_domain else f"https://{page_domain}").netloc
    except Exception:  # noqa: BLE001
        page_domain = scan.domain or ""

    audited = 0
    errors = 0
    skipped = 0

    for idx, (url, cited_count) in enumerate(pairs):
        if not url or not url.startswith(("http://", "https://")):
            skipped += 1
            continue

        fetched = fetch_page(url)
        status = fetched["status"]
        err = fetched["error"]
        html = fetched["html"]
        title = None
        lang = None
        audit_payload: dict = {}
        geo_score = None

        if html and not err:
            try:
                result = analyze_page(html, url, page_domain=page_domain)
                title = result.get("title")
                lang = result.get("lang")
                geo_score = result.get("geo_score")
                audit_payload = {
                    "signals": result.get("signals", {}),
                    "scores": result.get("scores", {}),
                    "issues": result.get("issues", []),
                }
            except Exception:  # noqa: BLE001 - never crash, log row with empty audit
                logger.exception(f"audit analyze failed for {url}")
                errors += 1
                err = "analyze_error"
        else:
            errors += 1

        # Upsert via raw delete+insert to keep ORM logic dumb. The UNIQUE
        # constraint guarantees we won't end up with duplicate rows.
        existing = (
            db.query(ScanPageAudit)
            .filter(ScanPageAudit.scan_id == scan_id, ScanPageAudit.url == url)
            .first()
        )
        if existing:
            existing.title = title or existing.title
            existing.lang = lang
            existing.fetch_status = status
            existing.fetch_error = err
            existing.audit = audit_payload
            existing.geo_score = geo_score
            existing.citation_count = cited_count
        else:
            db.add(ScanPageAudit(
                scan_id=scan_id,
                url=url,
                title=title,
                lang=lang,
                fetch_status=status,
                fetch_error=err,
                audit=audit_payload,
                geo_score=geo_score,
                citation_count=cited_count,
            ))

        audited += 1
        # Commit every 10 pages so progress is visible mid-run + crash
        # recovery doesn't lose everything.
        if audited % 10 == 0:
            db.commit()
            logger.info(f"audit progress {audited}/{len(pairs)}")

        time.sleep(PAGE_DELAY_SECONDS)

    db.commit()
    logger.info(f"audit complete : audited={audited} errors={errors} skipped={skipped}")
    return {
        "audited": audited,
        "errors": errors,
        "skipped": skipped,
        "total": len(pairs),
    }
