"""One-shot backfill : seed client_brands.brief from the workspace client_brief.

Phase BB. For every primary brand on every client that has a workspace brief
(client.apps['client_brief']) and an empty per-brand brief, assemble a
*partial* BrandBrief by extracting brand-specific fields from the workspace
brief :

- description          ← matching entry in workspace.primary_brands[].description
- editorial_voice      ← workspace.editorial_voice (inherited)
- target_audience      ← workspace.target_audience (inherited)
- positioning_statement← workspace.brand_positioning (inherited)
- direct_competitors   ← workspace.key_competitors mapped to CompetitorInBrief shape
- parent_group         ← workspace.company_overview first word? skip when unclear

The result is **partial by design** — generations_count stays at 0 so the UI
suggests the user clicks "Generate" on each brand row to enrich with web search.
We're seeding so brand briefs aren't NULL at downstream wire-up, NOT replacing
the LLM-driven generation step.

Run :
    docker compose exec -T worker python scripts/backfill_brand_briefs.py
    docker compose exec -T worker python scripts/backfill_brand_briefs.py --dry-run
    docker compose exec -T worker python scripts/backfill_brand_briefs.py --client <uuid>
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Ensure we can `import` modules from worker/ without `worker.` prefix
_WORKER_DIR = Path(__file__).resolve().parent.parent
if str(_WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKER_DIR))

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from models import Client, ClientBrand, Scan, ScanBrandClassification, get_db


def _brand_level_competitors(brand: ClientBrand, db: Session) -> list[dict]:
    """Resolve the brand-level competitors from the Gate-2 classifications.

    Avoids the workspace.key_competitors trap : those are group-level
    (L'Oréal, Sanofi…) because the workspace brief describes the COMPANY.
    For a per-brand brief, we want BRAND-level competitors (Bioderma,
    La Roche-Posay…) — they live in ScanBrandClassification across all
    scans of this client. Order by tag frequency, exclude self / children
    / primaries.
    """
    from collections import Counter

    client = db.query(Client).filter(Client.id == brand.client_id).first()
    primary_ids = {str(b) for b in (client.primary_brand_ids if client else []) or []}
    scan_ids = [s.id for s in db.query(Scan).filter(Scan.client_id == brand.client_id).all()]
    if not scan_ids:
        return []

    rows = (
        db.query(ScanBrandClassification.brand_id)
        .filter(
            ScanBrandClassification.scan_id.in_(scan_ids),
            ScanBrandClassification.classification == "competitor",
        )
        .all()
    )
    counter = Counter(str(r.brand_id) for r in rows if r.brand_id)
    if not counter:
        return []

    top_ids = [bid for bid, _ in counter.most_common(30)]
    child_ids = {
        str(c.id) for c in db.query(ClientBrand.id).filter(
            ClientBrand.parent_id == brand.id
        ).all()
    }
    candidates = (
        db.query(ClientBrand)
        .filter(ClientBrand.id.in_(top_ids))
        .all()
    )
    by_id = {str(c.id): c for c in candidates}

    out: list[dict] = []
    for bid, _ in counter.most_common():
        if bid == str(brand.id) or bid in child_ids or bid in primary_ids:
            continue
        row = by_id.get(bid)
        if not row or not row.name or row.parent_id is not None:
            continue
        out.append({
            "name": row.name,
            "products": list(row.product_lines or [])[:5],
            "domain": (row.domain or "").strip(),
        })
        if len(out) >= 10:
            break
    return out


def _build_partial_brief(brand: ClientBrand, workspace_brief: dict, db: Session) -> dict:
    """Assemble a minimal BrandBrief dict from the workspace brief.

    Conservative split : we copy general workspace fields onto every primary
    brand and surface the brand-specific entry from workspace.primary_brands
    when names match. direct_competitors comes from Gate-2 classifications,
    NOT from workspace.key_competitors (which are group-level). The result
    satisfies the Pydantic shape but generations_count stays 0 so the UI
    nudges the user to regenerate with web search.
    """
    out: dict = {
        "name": brand.name,
        "parent_group": "",
        "description": "",
        "founded_year": None,
        "headquarters": "",
        "languages": [],
        "positioning_statement": "",
        "taglines": [],
        "differentiators": [],
        "price_tier": "",
        "distribution": [],
        # Voice + audience inherited from workspace — brand override comes later
        "editorial_voice": (workspace_brief.get("editorial_voice") or "").strip(),
        "tonality": [],
        "target_audience": (workspace_brief.get("target_audience") or "").strip(),
        "audience_segments": [],
        "product_lines": list(brand.product_lines or []),
        "hero_products": [],
        "signature_features": [],
        "direct_competitors": [],
        "indirect_competitors": [],
        "expertise_topics": [],
        "regulatory_constraints": [],
    }

    # Try to recover the per-brand description from workspace.primary_brands[]
    name_lc = (brand.name or "").lower().strip()
    for entry in (workspace_brief.get("primary_brands") or []):
        if isinstance(entry, dict) and (entry.get("name") or "").lower().strip() == name_lc:
            out["description"] = (entry.get("description") or "").strip()
            break

    # Workspace-level positioning becomes the brand-level positioning when
    # no specific positioning_statement is otherwise available. The merge
    # logic in brief_injector treats this as workspace inheritance anyway,
    # but persisting it on the brand keeps downstream readers consistent.
    if workspace_brief.get("brand_positioning"):
        out["positioning_statement"] = workspace_brief["brand_positioning"].strip()

    # BB.8 : prefer brand-level Gate-2 competitors over workspace.key_competitors.
    # workspace.key_competitors lists GROUPS (L'Oréal, Sanofi, …) — that's
    # correct for the workspace brief but wrong for a per-brand brief.
    # The Gate-2 classification surfaces BRANDS (Bioderma, LRP, Eucerin).
    out["direct_competitors"] = _brand_level_competitors(brand, db)

    # Provenance markers — generations_count stays 0 so the UI shows
    # "Click Generate to enrich". edited_by_user=False keeps regen unblocked.
    out["generated_via"] = "backfill"
    out["generated_at"] = datetime.utcnow().isoformat() + "Z"
    out["edited_by_user"] = False
    return out


def _has_meaningful_content(partial: dict) -> bool:
    """True when the partial brief carries at least one non-empty content field.

    A brand whose workspace brief lacks every signal still gets a row inserted,
    but we flag it for the user so they know to regen with the LLM.
    """
    return bool(
        partial.get("description")
        or partial.get("editorial_voice")
        or partial.get("target_audience")
        or partial.get("positioning_statement")
        or partial.get("product_lines")
        or partial.get("direct_competitors")
    )


def backfill_client(client: Client, db: Session, dry_run: bool = False) -> tuple[int, int, int]:
    """Backfill all primary brands for one client.

    Returns (inserted, skipped_already_briefed, no_content) counts.
    """
    apps = client.apps or {}
    workspace_brief = apps.get("client_brief") or {}
    if not workspace_brief:
        print(f"  [skip] client {client.id} ({client.name}) has no workspace brief — generate one first")
        return (0, 0, 0)

    primary_ids = list(client.primary_brand_ids or [])
    if not primary_ids:
        print(f"  [skip] client {client.id} ({client.name}) has no primary_brand_ids")
        return (0, 0, 0)

    inserted = 0
    skipped = 0
    no_content = 0
    for bid in primary_ids:
        brand = db.query(ClientBrand).filter(ClientBrand.id == bid).first()
        if not brand:
            continue
        if brand.brief is not None:
            skipped += 1
            continue
        partial = _build_partial_brief(brand, workspace_brief, db)
        if not _has_meaningful_content(partial):
            no_content += 1
            print(f"  [warn] brand {brand.name} → partial brief is empty, recommend manual regen")
        if not dry_run:
            brand.brief = partial
            # Leave brief_generated_at NULL — only LLM runs set that. brief_generations_count
            # stays 0 so the cap budget is unaffected.
            flag_modified(brand, "brief")
        inserted += 1
        print(f"  [{'dry' if dry_run else 'ok'}] brand {brand.name} ({brand.id}) seeded")

    if not dry_run:
        db.commit()
    return (inserted, skipped, no_content)


def main():
    parser = argparse.ArgumentParser(description="Backfill per-brand briefs from workspace briefs")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be inserted without committing")
    parser.add_argument("--client", type=str, default=None,
                        help="Restrict to a single client UUID")
    args = parser.parse_args()

    db: Session = next(get_db())
    try:
        q = db.query(Client)
        if args.client:
            q = q.filter(Client.id == args.client)
        clients = q.all()
        if not clients:
            print("No clients match the filter — aborting")
            return 1

        total_inserted = 0
        total_skipped = 0
        total_no_content = 0
        for c in clients:
            print(f"\nClient {c.id} — {c.name}")
            inserted, skipped, no_content = backfill_client(c, db, dry_run=args.dry_run)
            total_inserted += inserted
            total_skipped += skipped
            total_no_content += no_content

        print(f"\n========= BACKFILL SUMMARY ({'dry-run' if args.dry_run else 'committed'}) =========")
        print(f"  brands seeded         : {total_inserted}")
        print(f"  brands already briefed: {total_skipped}")
        print(f"  brands with empty payload (manual regen recommended): {total_no_content}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
