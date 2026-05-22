"""One-shot: fix the Ducray scan brief + brand classifications.

The Web Brief LLM listed all 10 Pierre Fabre sister brands as "Own brands"
(architectural bug; prompt fix already deployed for future scans, but the
existing scan's brief needs an in-place update). Also missing Luxéol from
the competitors list.

This script :
  1. Updates scan.config.domain_brief.brands → ["Ducray"]
  2. Updates scan.config.domain_brief.competitors → enriched (sister brands +
     Luxéol + main external competitors with their key products)
  3. Reclassifies the 4 named sister brands from `ignored` → `competitor`
  4. Upserts client_brands rows for any new competitor name/gamme + creates
     scan_brand_classifications rows with classification='competitor'

Run:
    SCAN_ID=cbe07877-c85c-4c25-9091-0274cd72c776 \
      docker exec senai-worker python /tmp/fix_ducray_brief.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

sys.path.insert(0, '/app')

from sqlalchemy import func
from sqlalchemy.orm.attributes import flag_modified
from models import SessionLocal, Scan, ClientBrand, ScanBrandClassification
from services.brand_name_norm import normalize_brand_name

SCAN_ID = os.environ.get("SCAN_ID", "")

# Refined competitor watchlist for the Ducray scan. Includes :
#   - 4 Pierre Fabre sister brands (user-validated): Avène, A-Derma,
#     Klorane, René Furterer
#   - Luxéol (capillaire competitor missed by the original brief)
#   - Major external competitors with their overlapping product lines
COMPETITORS_OVERRIDE = [
    {"name": "Eau Thermale Avène", "domain": "eau-thermale-avene.fr", "products": [
        "Cleanance", "Dexyane Avène", "Cicalfate", "Antirougeurs", "XeraCalm A.D",
    ]},
    {"name": "A-Derma", "domain": "aderma.fr", "products": [
        "Exomega", "Phys-AC", "Dermalibour", "Rheacalm",
    ]},
    {"name": "Klorane", "domain": "klorane.com", "products": [
        "Quinine", "Cystiphane", "Anti-pelliculaire",
    ]},
    {"name": "René Furterer", "domain": "renefurterer.com", "products": [
        "Triphasic", "Astera", "Forticea", "Naturia",
    ]},
    {"name": "Luxéol", "domain": "luxeol.fr", "products": [
        "Capillaire", "Anti-chute", "Cheveux et ongles",
    ]},
    {"name": "La Roche-Posay", "domain": "laroche-posay.fr", "products": [
        "Effaclar", "Toleriane", "Anthelios", "Lipikar",
    ]},
    {"name": "Vichy", "domain": "vichy.fr", "products": [
        "Normaderm", "Dercos", "Liftactiv", "Capital Soleil",
    ]},
    {"name": "Bioderma", "domain": "bioderma.fr", "products": [
        "Sébium", "Atoderm", "Sensibio", "Photoderm",
    ]},
    {"name": "Eucerin", "domain": "eucerin.fr", "products": [
        "DermoPure", "AtopiControl", "Sun Protection",
    ]},
    {"name": "Caudalie", "domain": "caudalie.com", "products": [
        "Vinopure", "Resveratrol-Lift",
    ]},
    {"name": "Nuxe", "domain": "nuxe.com", "products": [
        "Rêve de Miel",
    ]},
    {"name": "Uriage", "domain": "uriage.fr", "products": [
        "Hyséac",
    ]},
]

# Sister brands to PROMOTE from ignored → competitor (user-validated).
SISTER_BRANDS_TO_PROMOTE = [
    "eau thermale avene", "eau thermale avène",
    "a-derma", "aderma",
    "klorane",
    "rené furterer", "rene furterer",
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

    # 1. Update the brief in place.
    cfg = dict(scan.config or {})
    brief = dict(cfg.get("domain_brief") or {})
    brief["brands"] = ["Ducray"]  # the scanned brand ONLY
    brief["competitors"] = COMPETITORS_OVERRIDE
    cfg["domain_brief"] = brief
    cfg["domain_brief_manual_edit"] = datetime.utcnow().isoformat()
    scan.config = cfg
    flag_modified(scan, "config")
    print(f"→ Brief updated: brands=['Ducray'], competitors={len(COMPETITORS_OVERRIDE)} entries")

    # 2. Promote sister brand roots from ignored → competitor.
    promoted = 0
    for sb_name_low in SISTER_BRANDS_TO_PROMOTE:
        brand = db.query(ClientBrand).filter(
            ClientBrand.client_id == scan.client_id,
            ClientBrand.canonical_name == normalize_brand_name(sb_name_low),
            ClientBrand.parent_id.is_(None),
        ).first()
        if not brand:
            continue
        sbc = db.query(ScanBrandClassification).filter(
            ScanBrandClassification.scan_id == SCAN_ID,
            ScanBrandClassification.brand_id == brand.id,
        ).first()
        if sbc:
            if sbc.classification != "competitor":
                sbc.classification = "competitor"
                sbc.classified_by = "user_bulk"
                sbc.source = "user_promoted_sister"
                sbc.updated_at = datetime.utcnow()
                promoted += 1
        else:
            db.add(ScanBrandClassification(
                scan_id=SCAN_ID, brand_id=brand.id,
                classification="competitor", is_focus=False,
                classified_by="user_bulk", source="user_promoted_sister",
            ))
            promoted += 1
    print(f"→ Promoted {promoted} sister brands ignored→competitor")

    # 3. For each competitor in the override, upsert brand + gammes + SBC rows.
    created_brands = 0
    created_gammes = 0
    classified = 0
    skipped_my_brand = 0

    def _classify_as_competitor(brand_id):
        sbc = db.query(ScanBrandClassification).filter(
            ScanBrandClassification.scan_id == SCAN_ID,
            ScanBrandClassification.brand_id == brand_id,
        ).first()
        if sbc is None:
            db.add(ScanBrandClassification(
                scan_id=SCAN_ID, brand_id=brand_id,
                classification="competitor", is_focus=False,
                classified_by="brief", source="brief_manual_edit",
            ))
            return "classified"
        if sbc.classification == "my_brand" or sbc.is_focus:
            return "skipped_my_brand"
        if sbc.classification != "competitor":
            sbc.classification = "competitor"
            sbc.classified_by = "brief"
            sbc.source = "brief_manual_edit"
            sbc.updated_at = datetime.utcnow()
            return "classified"
        return "already_competitor"

    for comp in COMPETITORS_OVERRIDE:
        name = (comp.get("name") or "").strip()
        domain = (comp.get("domain") or "").strip().lower() or None
        if not name:
            continue

        # Upsert root brand via normalized canonical (handles accent + case)
        name_norm = normalize_brand_name(name)
        existing = db.query(ClientBrand).filter(
            ClientBrand.client_id == scan.client_id,
            ClientBrand.canonical_name == name_norm,
            ClientBrand.parent_id.is_(None),
        ).first()
        if not existing:
            existing = ClientBrand(
                client_id=scan.client_id,
                name=name, canonical_name=name_norm,
                domain=domain,
                detected_in_scan_id=SCAN_ID,
                auto_detected=True, validated_by_user=False,
                detection_source="brief_manual_edit",
                last_seen_at=datetime.utcnow(),
            )
            db.add(existing); db.flush()
            created_brands += 1
        else:
            existing.last_seen_at = datetime.utcnow()
            if domain and not existing.domain:
                existing.domain = domain
        root = existing

        action = _classify_as_competitor(root.id)
        if action == "classified":
            classified += 1
        elif action == "skipped_my_brand":
            skipped_my_brand += 1
            continue

        # Products → competitor_gamme children (also normalized)
        for prod_name in comp.get("products", []):
            prod = (prod_name or "").strip()
            if not prod or prod.lower() == name.lower():
                continue
            prod_norm = normalize_brand_name(prod)
            gamme = db.query(ClientBrand).filter(
                ClientBrand.client_id == scan.client_id,
                ClientBrand.canonical_name == prod_norm,
            ).first()
            if not gamme:
                gamme = ClientBrand(
                    client_id=scan.client_id,
                    name=prod, canonical_name=prod_norm,
                    parent_id=root.id,
                    detected_in_scan_id=SCAN_ID,
                    auto_detected=True, validated_by_user=False,
                    detection_source="brief_manual_edit",
                    last_seen_at=datetime.utcnow(),
                )
                db.add(gamme); db.flush()
                created_gammes += 1
            else:
                gamme.last_seen_at = datetime.utcnow()
                if gamme.parent_id is None:
                    gamme.parent_id = root.id
            _classify_as_competitor(gamme.id)

    scan.updated_at = datetime.utcnow()
    db.commit()

    print(f"\n=========================")
    print(f"SUMMARY (fix_ducray_brief)")
    print(f"  sister brands promoted (ignored→competitor) : {promoted}")
    print(f"  new competitor root brands created           : {created_brands}")
    print(f"  new gamme child brands created               : {created_gammes}")
    print(f"  SBC rows classified (new+reclassified)       : {classified}")
    print(f"  skipped (my_brand or focus)                  : {skipped_my_brand}")

    # Final report
    counts = {}
    for cls, in db.query(ScanBrandClassification.classification).filter(
        ScanBrandClassification.scan_id == SCAN_ID
    ).all():
        counts[cls] = counts.get(cls, 0) + 1
    print(f"\nFinal classification counts:")
    for cls, n in sorted(counts.items()):
        print(f"  {cls:15s} → {n}")
    print(f"=========================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
