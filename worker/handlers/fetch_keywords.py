"""Handler: fetch keywords from HaloScan API for a scan's domain."""

import asyncio
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from adapters.haloscan_client import fetch_domain_positions

logger = logging.getLogger(__name__)


def execute(job_payload: dict, scan_id: str, db: Session) -> dict:
    """Fetch keyword positions from HaloScan and store in scan_keywords."""
    domain = job_payload.get("domain")
    if not domain:
        raise ValueError("Missing 'domain' in job payload")

    max_position = job_payload.get("max_position", 50)
    max_urls = job_payload.get("max_urls", 2000)

    # Run async HaloScan call
    positions = asyncio.run(fetch_domain_positions(domain, limit=max_urls))

    from exceptions import PermanentScanError

    _no_data_msg = (
        f"No Google France SEO data found for {domain}. "
        f"This scanner uses HaloScan (google.fr only) — it can only analyze "
        f"sites that rank on Google France. Try a different domain that has "
        f"French SEO presence (e.g. *.fr or sites with content optimized for FR)."
    )

    if not positions:
        raise PermanentScanError(_no_data_msg)

    # HaloScan wraps results in a dict with metadata
    if isinstance(positions, dict):
        results = positions.get("results", [])
    else:
        results = positions

    if not results:
        raise PermanentScanError(_no_data_msg)

    # Import here to avoid circular imports at module level
    from models import ScanKeyword, Scan

    # Clear existing keywords for this scan
    db.query(ScanKeyword).filter(ScanKeyword.scan_id == scan_id).delete()

    # Insert new keywords
    count = 0
    for row in results:
        # HaloScan response fields can vary — handle flexibly
        keyword = row.get("keyword") or row.get("kw") or row.get("mot_cle")
        url = row.get("url") or row.get("page_url") or row.get("page") or row.get("landing_page") or ""
        position = _safe_int(row.get("position") or row.get("pos"))
        traffic = _safe_int(row.get("traffic") or row.get("trafic"))
        volume = _safe_int(row.get("volume") or row.get("search_volume") or row.get("volumeh") or row.get("ads_volume"))

        if not keyword:
            continue

        # Filter by max position
        if position is not None and position > max_position:
            continue

        db.add(ScanKeyword(
            scan_id=scan_id,
            url=url,
            keyword=keyword,
            position=position,
            traffic=traffic,
            search_volume=volume,
        ))
        count += 1

    # Update scan status
    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if scan:
        scan.status = "keywords_fetched"
        scan.updated_at = datetime.utcnow()

    db.commit()
    total_from_api = len(results)
    logger.info(f"Fetched {count} keywords (top {max_position}) from {total_from_api} total for {domain}")

    return {
        "keywords_count": count,
        "total_from_api": total_from_api,
        "max_position": max_position,
        "domain": domain,
    }


def _safe_int(val) -> int | None:
    if val is None:
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None
