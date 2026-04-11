"""Handler: detect competitors via HaloScan API and enrich Brand Registry."""

import asyncio
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from adapters.haloscan_client import fetch_site_competitors

logger = logging.getLogger(__name__)


def execute(job_payload: dict, scan_id: str, db: Session) -> dict:
    """Fetch SEO competitors from HaloScan and add to Brand Registry."""
    from models import Scan, ClientBrand, ScanBrandClassification

    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan:
        raise RuntimeError("Scan not found")

    scan.progress_message = "Détection des concurrents SEO..."
    db.commit()

    try:
        competitors = asyncio.run(fetch_site_competitors(scan.domain, limit=20))
    except Exception as e:
        logger.warning(f"HaloScan siteCompetitors failed for {scan.domain}: {e}")
        competitors = []

    new_brands = 0
    sbc_rows_added = 0
    own_root = (scan.domain or "").split("/")[0].replace("www.", "")
    for comp in competitors:
        # HaloScan siteCompetitors returns: {root_domain, url, total_traffic, visibility_index, ...}
        domain = comp.get("root_domain") or comp.get("url") or comp.get("domain") or ""
        if not domain:
            continue
        domain_clean = domain.replace("www.", "").split("/")[0]
        if not domain_clean or domain_clean == own_root:
            continue

        # Extract brand name from domain (e.g., "laroche-posay.fr" → "La Roche Posay")
        brand_name = domain_clean.split(".")[0].replace("-", " ").title()

        existing = db.query(ClientBrand).filter(
            ClientBrand.client_id == scan.client_id,
            ClientBrand.name == brand_name,
        ).first()

        if not existing:
            # Also check by domain
            existing = db.query(ClientBrand).filter(
                ClientBrand.client_id == scan.client_id,
                ClientBrand.domain == domain,
            ).first()

        now = datetime.utcnow()
        if not existing:
            brand = ClientBrand(
                client_id=scan.client_id,
                name=brand_name,
                canonical_name=brand_name,
                domain=domain_clean,
                detected_in_scan_id=scan_id,
                detection_source="haloscan_competitors",
                auto_detected=True,
                validated_by_user=False,
                last_seen_at=now,
            )
            db.add(brand)
            db.flush()  # get brand.id for SBC insert
            new_brands += 1
        else:
            # Refresh last_seen_at on existing row (don't touch name/category)
            existing.last_seen_at = now
            brand = existing

        # Upsert ScanBrandClassification for this scan+brand combo
        sbc_existing = db.query(ScanBrandClassification).filter(
            ScanBrandClassification.scan_id == scan_id,
            ScanBrandClassification.brand_id == brand.id,
        ).first()
        if not sbc_existing:
            db.add(ScanBrandClassification(
                scan_id=scan_id,
                brand_id=brand.id,
                classification="unclassified",
                is_focus=False,
                classified_by="auto",
                source="haloscan_competitors",
            ))
            sbc_rows_added += 1

    scan.updated_at = datetime.utcnow()

    # Chain cleanup_brands to auto-classify any unclassified brands using Claude
    # (existing handler in cleanup_brands.py + brand_classifier.py adapter).
    # This runs in background while user validates topics → by the time they reach
    # the Brands gate, the inbox should be empty or near-empty.
    # cleanup_brands now operates per-scan, so chain it whenever the API returned
    # at least one competitor (new SBC rows may need classification even if no
    # new client_brands rows were added).
    if len(competitors) > 0:
        from models import Job
        db.add(Job(scan_id=scan_id, job_type="cleanup_brands"))

    db.commit()

    logger.info(f"Detected {new_brands} new competitors for {scan.domain}")
    return {
        "competitors_from_api": len(competitors),
        "new_brands_added": new_brands,
        "sbc_rows_added": sbc_rows_added,
    }
