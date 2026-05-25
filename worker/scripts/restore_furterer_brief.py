"""One-shot: set the René Furterer scan to a curated pharmacy/retail competitor
set and RESET its competitor classifications to exactly that set.

The auto-brief came back salon-skewed (Redken/Matrix/Wella/Schwarzkopf/Paul
Mitchell/Aveda) and missed RF's real retail rivals. User chose "set pharmacie
pur": keep only the curated retail competitors, drop the salon-only ones.

Like restore_klorane_brief: sets the brief + classifies the curated set, then
deletes every competitor SBC row whose brand is NOT in the curated set (roots +
gammes, except focus). Scan-scoped (SBC only; ClientBrand rows kept).

Run:
    SCAN_ID=74b6f9ca-c7e6-4577-942b-772cfab8a58f \
      docker exec senai-worker python /tmp/restore_furterer_brief.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, '/app')

from sqlalchemy import or_
from sqlalchemy.orm.attributes import flag_modified
from models import SessionLocal, Scan, ClientBrand, ScanBrandClassification
from services.brand_name_norm import normalize_brand_name

SCAN_ID = os.environ.get("SCAN_ID", "")

# Curated RF competitive set — pharmacy/parapharmacie hair + premium, named gammes.
# Avène/A-Derma excluded (no competing hair range); salon-only brands dropped.
CURATED = [
    {"name": "Klorane", "domain": "klorane.com", "products": [
        "Quinine", "Ortie", "Galanga", "Mangue"]},
    {"name": "Ducray", "domain": "ducray.com", "products": [
        "Anaphase", "Neoptide", "Squanorm", "Kelual DS"]},
    {"name": "Kérastase", "domain": "kerastase.fr", "products": [
        "Nutritive", "Spécifique", "Genesis", "Résistance"]},
    {"name": "Phyto", "domain": "phyto.com", "products": [
        "Phytocyane", "Phytonovathrix", "Phytophanère", "Phytodéfrisant"]},
    {"name": "Luxéol", "domain": "luxeol.fr", "products": [
        "Anti-chute", "Cheveux et ongles", "Pousse"]},
    {"name": "Vichy", "domain": "vichy.fr", "products": [
        "Dercos Aminexil", "Dercos Densi-Solutions", "Dercos Anti-pelliculaire"]},
    {"name": "L'Oréal Professionnel", "domain": "lorealprofessionnel.fr", "products": [
        "Serie Expert", "Metal Detox"]},
    {"name": "Forté Pharma", "domain": "fortepharma.com", "products": ["Forcapil"]},
    {"name": "Nioxin", "domain": "nioxin.com", "products": ["System Kit"]},
]


def main():
    if not SCAN_ID:
        print("ERROR: SCAN_ID env var required")
        return 1
    db = SessionLocal()
    scan = db.query(Scan).filter(Scan.id == SCAN_ID).first()
    if not scan:
        print(f"ERROR: scan {SCAN_ID} not found")
        return 1
    print(f"Scan: {scan.id} ({scan.domain})")

    desired_norms = set()
    for comp in CURATED:
        desired_norms.add(normalize_brand_name(comp["name"]))
        for p in comp.get("products", []):
            desired_norms.add(normalize_brand_name(p))

    # 1. Restore the curated brief, protect it from auto-regen.
    cfg = dict(scan.config or {})
    brief = dict(cfg.get("domain_brief") or {})
    brief["brands"] = ["René Furterer"]
    brief["competitors"] = CURATED
    brief["edited_by_user"] = True
    cfg["domain_brief"] = brief
    cfg["domain_brief_provider"] = "manual_curated"
    cfg["domain_brief_manual_edit"] = datetime.utcnow().isoformat()
    scan.config = cfg
    flag_modified(scan, "config")
    print(f"→ Brief set: brands=['René Furterer'], competitors={len(CURATED)}, edited_by_user=True")

    def _get_or_create_brand(name, domain=None, parent_id=None):
        name = (name or "").strip()
        if not name:
            return None
        name_norm = normalize_brand_name(name)
        existing = db.query(ClientBrand).filter(
            ClientBrand.client_id == scan.client_id,
            or_(ClientBrand.name == name, ClientBrand.canonical_name == name_norm),
        ).first()
        if existing:
            existing.last_seen_at = datetime.utcnow()
            if domain and not existing.domain:
                existing.domain = domain
            if parent_id and existing.parent_id is None:
                existing.parent_id = parent_id
            return existing
        b = ClientBrand(
            client_id=scan.client_id, name=name, canonical_name=name_norm,
            domain=domain, parent_id=parent_id, detected_in_scan_id=SCAN_ID,
            auto_detected=True, validated_by_user=False,
            detection_source="manual_curated", last_seen_at=datetime.utcnow(),
        )
        db.add(b); db.flush()
        return b

    def _ensure_competitor(brand_id):
        sbc = db.query(ScanBrandClassification).filter(
            ScanBrandClassification.scan_id == SCAN_ID,
            ScanBrandClassification.brand_id == brand_id,
        ).first()
        if sbc is None:
            db.add(ScanBrandClassification(
                scan_id=SCAN_ID, brand_id=brand_id, classification="competitor",
                is_focus=False, classified_by="user_bulk", source="manual_curated"))
            return
        if sbc.is_focus or sbc.classification == "my_brand":
            return
        if sbc.classification != "competitor":
            sbc.classification = "competitor"
            sbc.classified_by = "user_bulk"
            sbc.source = "manual_curated"
            sbc.updated_at = datetime.utcnow()

    # 2. Upsert + classify the curated set.
    for comp in CURATED:
        root = _get_or_create_brand(comp["name"], domain=(comp.get("domain") or "").lower() or None)
        _ensure_competitor(root.id)
        for prod in comp.get("products", []):
            if not prod or prod.lower() == comp["name"].lower():
                continue
            g = _get_or_create_brand(prod, parent_id=root.id)
            if g:
                _ensure_competitor(g.id)

    # 3. RESET — delete competitor SBC rows whose brand is not in the curated set
    # (drops the salon-skewed auto-brief competitors + their gammes). Except focus.
    deleted = kept = 0
    for sbc in db.query(ScanBrandClassification).filter(
        ScanBrandClassification.scan_id == SCAN_ID,
        ScanBrandClassification.classification == "competitor",
    ).all():
        if sbc.is_focus:
            kept += 1
            continue
        b = db.query(ClientBrand).filter(ClientBrand.id == sbc.brand_id).first()
        if b and normalize_brand_name(b.name) in desired_norms:
            kept += 1
            continue
        db.delete(sbc)
        deleted += 1
    print(f"→ Reset competitors: kept {kept} (curated), deleted {deleted} (salon-skew auto-brief)")

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
