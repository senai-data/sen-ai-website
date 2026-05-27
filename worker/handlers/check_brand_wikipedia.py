"""Handler: check the Wikipedia presence of a scan's brands.

Sprint 4 (project_10_action_features.md #1). Looks up each classified brand
of the scan (focus + competitors + my_brand children) on Wikipedia FR + EN,
caches the result in client_brands.wikipedia JSONB with a 7-day TTL.

Triggered :
- Chained automatically from cleanup_brands at the end of run_llm_tests
- Manually re-enqueable per scan for refresh

Idempotent - skips any brand whose `wikipedia.checked_at` is < 7 days old
unless `force=true` is passed in the job payload.

Cost : zero LLM. Plain Wikipedia REST + Action API (free, no auth). ~2-3
requests per brand per language ≈ 6 requests/brand for FR+EN. With 20 brands
per scan that's 120 polite HTTP calls - under Wikipedia's 200 req/s ceiling
even sequentially.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from adapters.wikipedia_client import check_brand_wikipedia

logger = logging.getLogger(__name__)

TTL_DAYS = 7

# Per-brand throttle. Wikipedia opensearch rate-limits aggressively (~5-10
# anonymous req/s per IP). With ~6 calls per brand (opensearch + summary +
# page_info, × 2 langs) we need ≥ 600 ms between brands to stay polite.
BRAND_DELAY_SECONDS = 0.6

# Which classifications we audit by default. The actual enum used in
# scan_brand_classifications is { competitor, my_brand, ignored, unclassified }
# - the model docstring mentions older labels (target_brand etc.) that aren't
# emitted in practice. We audit only competitor + my_brand by default ;
# `unclassified` and `ignored` would hammer Wikipedia with noise.
DEFAULT_CLASSIFICATIONS = (
    "my_brand",
    "competitor",
)


def _is_fresh(wiki_payload: dict | None) -> bool:
    """Skip the lookup if we already checked this brand within TTL_DAYS."""
    if not isinstance(wiki_payload, dict):
        return False
    checked = wiki_payload.get("checked_at")
    if not checked:
        return False
    try:
        ts = datetime.fromisoformat(checked.rstrip("Z"))
    except ValueError:
        return False
    return ts > (datetime.utcnow() - timedelta(days=TTL_DAYS))


def execute(job_payload: dict, scan_id: str, db: Session) -> dict:
    """Refresh the Wikipedia presence cache for a scan's brands.

    job_payload :
      - force (bool, default False) : ignore the TTL, refresh every brand
      - langs (list[str], default ["fr","en"]) : languages to check
    """
    from models import Scan, ClientBrand, ScanBrandClassification

    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan:
        raise RuntimeError("Scan not found")

    force = bool(job_payload.get("force"))
    langs = job_payload.get("langs") or ["fr", "en"]
    # Caller can override the classification filter ; default = classified
    # brands only (focus + targets + competitors). Set include_all=true to
    # also audit `discovered` / `unclassified` rows (heavy on Wikipedia,
    # not recommended for scans with hundreds of noise brands).
    include_all = bool(job_payload.get("include_all"))
    classifications = tuple(job_payload.get("classifications") or DEFAULT_CLASSIFICATIONS)

    # Pull brands attached to this scan. By default we restrict to :
    #   - the focus brand (always, regardless of parent_id), AND
    #   - master brands (parent_id IS NULL) classified as my_brand / competitor.
    # Sub-products / gammes (parent_id IS NOT NULL) are excluded because they
    # almost never have a dedicated Wikipedia page - Wikipedia forbids
    # commercial product entries - and they generate constant false positives
    # via name collisions ('Anaphase+' brand vs Anaphase the mitosis phase,
    # 'Keracnyl' brand vs Keracyanin the molecule, etc.).
    # include_all=true bypasses both filters (debug / power user).
    base_q = (
        db.query(ScanBrandClassification, ClientBrand)
        .join(ClientBrand, ClientBrand.id == ScanBrandClassification.brand_id)
        .filter(ScanBrandClassification.scan_id == scan_id)
    )
    if not include_all:
        base_q = base_q.filter(
            ScanBrandClassification.classification.in_(classifications)
        ).filter(
            or_(
                ScanBrandClassification.is_focus.is_(True),
                ClientBrand.parent_id.is_(None),
            )
        )
    sbc_rows = base_q.all()
    if not sbc_rows:
        logger.info(f"check_brand_wikipedia: no SBC rows for scan {scan_id}")
        return {"checked": 0, "skipped": 0, "errors": 0}

    checked = 0
    skipped = 0
    errors = 0

    for _sbc, brand in sbc_rows:
        # Idempotency : honor TTL unless force=true
        if not force and _is_fresh(brand.wikipedia):
            skipped += 1
            continue
        # Use the user-facing brand name (not canonical) - Wikipedia opensearch
        # handles accents/case better than the lowercased canonical_name. If
        # both opensearch attempts fail, we'll fall back to a "not found"
        # entry in the cache, which is still meaningful.
        query = brand.name or brand.canonical_name or ""
        if not query.strip():
            errors += 1
            continue
        try:
            payload = check_brand_wikipedia(
                query,
                langs=langs,
                brand_domain=brand.domain,
            )
            brand.wikipedia = payload
            flag_modified(brand, "wikipedia")
            checked += 1
            # Log a short summary line per brand - cheaper than dumping full payload.
            langs_status = ", ".join(
                f"{lg}={'ok' if (payload['by_lang'].get(lg, {}).get('exists')) else 'no'}"
                for lg in langs
            )
            logger.info(f"wikipedia[{brand.name}] {langs_status}")
        except Exception:  # noqa: BLE001 - never crash, surface as error count
            logger.exception(f"wikipedia check failed for brand {brand.id} ({brand.name})")
            errors += 1
        # Polite throttle between brands so we don't trip Wikipedia's
        # anonymous rate limiter mid-run.
        time.sleep(BRAND_DELAY_SECONDS)

    db.commit()
    return {
        "checked": checked,
        "skipped": skipped,
        "errors": errors,
        "total_brands": len(sbc_rows),
    }
