"""One-shot: configure ONE Pierre Fabre Oral Care brand's scan (Option B).

Sets the target brand as FOCUS (the ⭐ star) + my_brand + promotion, installs the
brand's CATEGORY-SPECIFIC competitor watchlist, and clean-replaces competitor
classifications to exactly that set. Sister oral brands are NOT competitors
(different categories) → not added.

Run:
    SCAN_ID=<uuid> BRAND=elg \
      docker exec senai-worker python /tmp/fix_oralcare_brief.py
  (BRAND = elg | ina | art | elu)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '/app')

from sqlalchemy import or_
from sqlalchemy.orm.attributes import flag_modified
from models import SessionLocal, Scan, ClientBrand, ScanBrandClassification
from services.brand_name_norm import normalize_brand_name
from oralcare_brands import get_brand

SCAN_ID = os.environ.get("SCAN_ID", "")
BRAND = os.environ.get("BRAND", "")


def main():
    if not SCAN_ID or not BRAND:
        print("ERROR: SCAN_ID and BRAND env vars required")
        return 1
    cfg = get_brand(BRAND)
    brand_name = cfg["brand_name"]
    competitors = cfg["competitors"]

    db = SessionLocal()
    scan = db.query(Scan).filter(Scan.id == SCAN_ID).first()
    if not scan:
        print(f"ERROR: scan {SCAN_ID} not found")
        return 1
    print(f"Scan: {scan.id} ({scan.domain}) — brand={brand_name}")

    desired_norms = set()
    for c in competitors:
        desired_norms.add(normalize_brand_name(c["name"]))
        for p in c.get("products", []):
            desired_norms.add(normalize_brand_name(p))

    def _get_or_create(name, domain=None, parent_id=None):
        name = (name or "").strip()
        if not name:
            return None
        nn = normalize_brand_name(name)
        b = db.query(ClientBrand).filter(
            ClientBrand.client_id == scan.client_id,
            or_(ClientBrand.name == name, ClientBrand.canonical_name == nn),
        ).first()
        if b:
            b.last_seen_at = datetime.utcnow()
            if domain and not b.domain:
                b.domain = domain
            if parent_id and b.parent_id is None:
                b.parent_id = parent_id
            return b
        b = ClientBrand(client_id=scan.client_id, name=name, canonical_name=nn, domain=domain,
                        parent_id=parent_id, detected_in_scan_id=SCAN_ID, auto_detected=True,
                        validated_by_user=False, detection_source="manual_curated", last_seen_at=datetime.utcnow())
        db.add(b); db.flush()
        return b

    def _set_class(brand_id, classification, focus=False):
        sbc = db.query(ScanBrandClassification).filter(
            ScanBrandClassification.scan_id == SCAN_ID,
            ScanBrandClassification.brand_id == brand_id,
        ).first()
        if sbc is None:
            db.add(ScanBrandClassification(scan_id=SCAN_ID, brand_id=brand_id, classification=classification,
                                           is_focus=focus, classified_by="user_bulk", source="manual_curated"))
        else:
            sbc.classification = classification
            sbc.classified_by = "user_bulk"
            sbc.source = "manual_curated"
            if focus:
                sbc.is_focus = True
            sbc.updated_at = datetime.utcnow()

    # 1. Brief.
    c = dict(scan.config or {})
    brief = dict(c.get("domain_brief") or {})
    brief["brands"] = [brand_name]
    brief["competitors"] = competitors
    brief["edited_by_user"] = True
    c["domain_brief"] = brief
    c["domain_brief_provider"] = "manual_curated"
    c["domain_brief_manual_edit"] = datetime.utcnow().isoformat()
    scan.config = c
    flag_modified(scan, "config")

    # 2. Target brand = FOCUS (star) + my_brand + promotion. Clear any other focus first
    # (DB enforces one is_focus per scan).
    for sbc in db.query(ScanBrandClassification).filter(
        ScanBrandClassification.scan_id == SCAN_ID, ScanBrandClassification.is_focus == True).all():
        sbc.is_focus = False
    db.flush()
    target = _get_or_create(brand_name, domain=scan.domain.split("/")[0])
    _set_class(target.id, "my_brand", focus=True)
    db.flush()
    scan.focus_brand_id = target.id
    scan.promotion_brand_ids = [target.id]
    print(f"→ Focus/star + promotion = {brand_name}")

    # 3. Competitors (+ gammes), clean-replace.
    for comp in competitors:
        root = _get_or_create(comp["name"], domain=(comp.get("domain") or "").lower() or None)
        _set_class(root.id, "competitor")
        for prod in comp.get("products", []):
            if not prod or prod.lower() == comp["name"].lower():
                continue
            g = _get_or_create(prod, parent_id=root.id)
            if g:
                _set_class(g.id, "competitor")

    deleted = kept = 0
    for sbc in db.query(ScanBrandClassification).filter(
        ScanBrandClassification.scan_id == SCAN_ID,
        ScanBrandClassification.classification == "competitor").all():
        if sbc.is_focus:
            kept += 1; continue
        b = db.query(ClientBrand).filter(ClientBrand.id == sbc.brand_id).first()
        if b and normalize_brand_name(b.name) in desired_norms:
            kept += 1; continue
        db.delete(sbc); deleted += 1
    print(f"→ Competitors: kept {kept} (curated), deleted {deleted} (off-brand auto)")

    # 4. The shared PF site tags ALL its brands my_brand. Keep only the target +
    # its own lines as my_brand; ignore the sisters/umbrella/other PF oral brands
    # (each tracked in its own scan). Keeps this scan's "My Brands" = the brand.
    tgt_norm = normalize_brand_name(brand_name)
    ignored = 0
    for sbc in db.query(ScanBrandClassification).filter(
        ScanBrandClassification.scan_id == SCAN_ID,
        ScanBrandClassification.classification == "my_brand").all():
        if sbc.is_focus:
            continue
        b = db.query(ClientBrand).filter(ClientBrand.id == sbc.brand_id).first()
        if not b:
            continue
        keep = (b.id == target.id or b.parent_id == target.id
                or normalize_brand_name(b.name).startswith(tgt_norm))
        if keep:
            continue
        sbc.classification = "ignored"
        sbc.classified_by = "user_bulk"
        sbc.source = "oralcare_sister_ignore"
        sbc.updated_at = datetime.utcnow()
        ignored += 1
    print(f"→ Non-target PF brands ignored: {ignored}")

    scan.updated_at = datetime.utcnow()
    db.commit()

    counts = {}
    for cls, in db.query(ScanBrandClassification.classification).filter(
        ScanBrandClassification.scan_id == SCAN_ID).all():
        counts[cls] = counts.get(cls, 0) + 1
    print(f"\nFinal classification counts: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
