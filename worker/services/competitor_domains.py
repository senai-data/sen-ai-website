"""Competitor domain resolver — answers "which domains must NEVER appear as
citations in this scan's generated content?"

This is the HARD constraint side of the brand-bias defense (the SOFT side is
`services.trust_sources` which provides preferred-source hints to the LLM).
Where trust_sources guides the LLM toward authoritative citations,
competitor_domains is the post-filter that drops anything the LLM still
returned on a competitor brand's website — even if the URL otherwise looks
authoritative.

Source of truth : `scan_brand_classifications.classification = 'competitor'`,
joined with `client_brands.domain`. Per-scan, deterministic. Updated by the
classify_topics + cleanup_brands handlers + the user's manual brand
classification UI (workspace settings + per-scan Gate 3).

Used by :
  - worker/handlers/generate_faq.py — both `_fetch_brand_context` and
    `_fetch_scientific_context` post-filter their web_search URLs against
    this set
  - (future) worker/handlers/generate_geo_article.py — same pattern

Why per-scan and not per-client : the same brand may be classified as my_brand
on one scan and competitor on another (e.g., agency managing multiple
clients with overlapping brands). The classification is the per-scan source
of truth, so the denylist must follow.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def get_competitor_domains_for_scan(scan_id, db: Session) -> set[str]:
    """Return the set of bare domains (lowercase, www stripped) classified as
    `competitor` for this scan.

    Returns an empty set when scan_id is falsy, the scan doesn't exist, or
    no brands are classified competitor. An empty set means "no hard
    denylist" — callers fall back to universal e-commerce / social patterns
    only (which is still meaningful protection for own-brand scans).

    Args:
        scan_id: UUID or str
        db: SQLAlchemy session

    Returns:
        set[str] of bare domains. Each entry is suitable for substring or
        suffix match — callers typically check `domain == d or
        domain.endswith("." + d)` against URL netlocs.
    """
    if not scan_id:
        return set()

    # Local imports keep the worker bootstrap path light (this module loads
    # at handler call time, not at registration time).
    from models import ClientBrand, ScanBrandClassification

    rows = (
        db.query(ClientBrand.domain)
        .join(
            ScanBrandClassification,
            ScanBrandClassification.brand_id == ClientBrand.id,
        )
        .filter(
            ScanBrandClassification.scan_id == scan_id,
            ScanBrandClassification.classification == "competitor",
            ClientBrand.domain.isnot(None),
        )
        .all()
    )

    domains: set[str] = set()
    for (raw,) in rows:
        if not raw or not isinstance(raw, str):
            continue
        d = raw.strip().lower()
        if d.startswith("http://"):
            d = d[7:]
        elif d.startswith("https://"):
            d = d[8:]
        if d.startswith("www."):
            d = d[4:]
        d = d.split("/", 1)[0].rstrip(".")
        if d and "." in d:
            domains.add(d)

    if domains:
        logger.info(
            "competitor_domains: scan %s → %d domain(s) (%s%s)",
            scan_id, len(domains),
            ", ".join(sorted(domains)[:5]),
            "..." if len(domains) > 5 else "",
        )
    else:
        logger.info(
            "competitor_domains: scan %s → 0 (own-brand scan or no SBC "
            "classifications yet — universal e-commerce/social filter still applies)",
            scan_id,
        )

    return domains
