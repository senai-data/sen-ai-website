"""Quick smoke : reconstruct the EXACT block injected into the article-gen prompt
for one selected ScanContentItem.

Validates the BB.4 wire-up end-to-end without spending $0.30 on a real Claude call.
The script :
  1. Picks one item (or accepts --item-id)
  2. Loads scan + client + focus_brand + brand.brief
  3. Calls format_workspace_brief(client.apps, focus.brief) — same fn the handler uses
  4. Calls format_promoted_brands_block(promoted_brand_names) — same fn the handler uses
  5. Prints the concatenated prefix that goes into seo_llm.NetlinkingArticleGenerator

If the brand brief content (heritage, tone DOs/DONTs, hero products) shows up
in this block, the BB.4 integration is functional. If it's missing, something
upstream broke (focus_brand_id NULL, brief NULL, merge dropping fields, etc.).

Run :
  docker compose exec -T worker python scripts/smoke_bb_article_prompt.py
  docker compose exec -T worker python scripts/smoke_bb_article_prompt.py --item-id <uuid>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_WORKER_DIR = Path(__file__).resolve().parent.parent
if str(_WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKER_DIR))

from models import get_db, Client, ClientBrand, Scan, ScanContentItem
from adapters.brief_injector import format_workspace_brief, format_promoted_brands_block
from services.brand_resolver import resolve_promotion, PromotionUnsetError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--item-id", type=str, default=None,
                        help="ScanContentItem UUID — defaults to first ungenerated article on Avène scan")
    parser.add_argument("--scan-id", type=str,
                        default="90604b64-021c-441a-85df-cc0623de95fd",
                        help="Fallback scan when --item-id is not given")
    args = parser.parse_args()

    db = next(get_db())

    if args.item_id:
        item = db.query(ScanContentItem).filter(ScanContentItem.id == args.item_id).first()
    else:
        item = (
            db.query(ScanContentItem)
            .filter(
                ScanContentItem.scan_id == args.scan_id,
                ScanContentItem.content_html.is_(None),
                ScanContentItem.target_url.isnot(None),
                ScanContentItem.content_type == "netlinking_article",
            )
            .order_by(ScanContentItem.opportunity_score.desc().nullslast())
            .first()
        )
    if not item:
        print("No ungenerated article item with target_url found — aborting")
        return 1

    scan = db.query(Scan).filter(Scan.id == item.scan_id).first()
    client = db.query(Client).filter(Client.id == scan.client_id).first()
    focus = None
    if scan.focus_brand_id:
        focus = db.query(ClientBrand).filter(ClientBrand.id == scan.focus_brand_id).first()

    print("=" * 80)
    print("BB.4 SMOKE — article-gen prompt prefix preview")
    print("=" * 80)
    print(f"Item id          : {item.id}")
    print(f"Scan             : {scan.name} ({scan.domain})")
    print(f"Topic            : {item.topic_name}")
    print(f"Persona          : {item.persona_name}")
    print(f"Target question  : {item.target_question}")
    print(f"Target URL       : {item.target_url}")
    print(f"Focus brand      : {focus.name if focus else '(none — workspace-only render)'}")
    print(f"Brand brief set  : {bool(focus and focus.brief)}")
    if focus and focus.brief:
        print(f"Brief generated  : {focus.brief.get('generated_via')}")
        print(f"Brief size       : {len(str(focus.brief))} chars")
    print()

    # Resolve promotion the same way generate_article does
    promoted_brand_names = []
    try:
        promotion = resolve_promotion(scan, db)
        promoted_brand_names = [b.name for b in promotion.promote_brands if b.name]
    except PromotionUnsetError as e:
        print(f"⚠ Promotion unresolved: {e} — article would generate without explicit promote bias")

    workspace_brief_text = format_workspace_brief(
        client.apps if client else None,
        focus.brief if focus else None,
    )
    promoted_block = format_promoted_brands_block(promoted_brand_names)

    print("=" * 80)
    print("BLOCK 1/2 — workspace_brief_text (BB.4 merged workspace + brand brief)")
    print("=" * 80)
    print(workspace_brief_text)
    print()
    print("=" * 80)
    print("BLOCK 2/2 — promoted_brands_block (brands to bias content toward)")
    print("=" * 80)
    print(promoted_block)
    print()

    # ── Sanity checks : assert key BB.4 + BB.8 signals are present ───────
    print("=" * 80)
    print("SANITY CHECKS (BB.4 + BB.8 wire-up assertions)")
    print("=" * 80)
    checks = [
        ("Workspace company block present",
         "## Your company" in workspace_brief_text),
        ("Focus brand section present",
         (focus is not None and f"### Focus brand: {focus.name}" in workspace_brief_text)),
        ("Brand brief override marker present (editorial voice or audience)",
         "(override)" in workspace_brief_text),
        ("BB.8 narrative fields surfaced (heritage OR brand_story)",
         "Heritage:" in workspace_brief_text or "Brand story:" in workspace_brief_text),
        ("BB.8 tone fields surfaced (DOs OR DON'Ts)",
         "Tone DOs" in workspace_brief_text or "Tone DON'Ts" in workspace_brief_text),
        ("BB.8 hero/signature products surfaced",
         "Hero products:" in workspace_brief_text or "Signature features:" in workspace_brief_text),
        ("Promoted brands resolved",
         bool(promoted_brand_names)),
        ("Promoted brands block non-empty",
         bool(promoted_block.strip())),
    ]
    pass_count = 0
    for label, ok in checks:
        mark = "✓" if ok else "✗"
        print(f"  {mark} {label}")
        if ok:
            pass_count += 1
    print()
    print(f"PASS {pass_count}/{len(checks)}")
    return 0 if pass_count == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
