"""Dry-run of media_replacement.suggest() against a live ScanContentItem.

Picks the first netlinking_article item with empty/null target_url (the
NEEDS_MEDIA_URL case) and prints the top-K suggestions with breakdown.
Read-only — no DB writes. Safe to run anytime.

Usage inside senai-worker container :
    docker exec -w /app -e PYTHONPATH=/app senai-worker \\
      python /app/scripts/smoke_mr2_suggest_dryrun.py
"""

from __future__ import annotations

import json
import sys

from models import SessionLocal, ScanContentItem
from services.media_replacement import IntentNotEligibleError, suggest


def main() -> int:
    db = SessionLocal()
    try:
        # Try to find an item that's a real "needs media" candidate first.
        item = (
            db.query(ScanContentItem)
            .filter(
                ScanContentItem.content_type == "netlinking_article",
                ScanContentItem.target_question.isnot(None),
            )
            .order_by(ScanContentItem.created_at.desc())
            .first()
        )
        if not item:
            print("No netlinking_article item found — populate a scan first")
            return 1

        print(f"=== Target item ===")
        print(f"  id           : {item.id}")
        print(f"  scan_id      : {item.scan_id}")
        print(f"  topic        : {item.topic_name}")
        print(f"  persona      : {item.persona_name}")
        print(f"  target_url   : {item.target_url}")
        print(f"  status       : {item.status}")
        print(f"  target_q     : {(item.target_question or '')[:120]}...")

        for strategy in ("match_competitor", "avoid_competitor"):
            for require_price in (False, True):
                print(f"\n=== suggest(strategy={strategy!r}, require_price={require_price}) ===")
                try:
                    out = suggest(
                        db,
                        content_item=item,
                        strategy=strategy,
                        require_price=require_price,
                        top_k=5,
                    )
                except IntentNotEligibleError as e:
                    print(f"  IntentNotEligibleError: {e}")
                    continue

                diag = out["diagnostics"]
                print(f"  diagnostics: country={diag.get('country')} language={diag.get('language')}")
                print(f"  raw={diag.get('candidates_raw')} after_filter={diag.get('candidates_after_filter')} "
                      f"scored={diag.get('candidates_scored')}")
                drops = diag.get("drop_reasons", {})
                if drops:
                    print(f"  drop_reasons: {drops}")

                if not out["suggestions"]:
                    print("  (no suggestions)")
                    continue

                print(f"  TOP {len(out['suggestions'])} :")
                for i, s in enumerate(out["suggestions"], 1):
                    price = f"{s['price_eur']:.0f}€" if s['price_eur'] else "—"
                    da = s['da'] if s['da'] is not None else "—"
                    print(f"  {i}. {s['domain']:35s} score={s['score']:.2f}  "
                          f"DA={da:>3}  price={price:>6}  badge={s['authority_badge']:7s}  "
                          f"src={s['source']}")
                    for r in s['breakdown']['reasons'][:3]:
                        print(f"       ✓ {r}")
                    for r in s['breakdown']['risks'][:2]:
                        print(f"       ⚠ {r}")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
