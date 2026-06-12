"""One-shot dedupe of scan_content_items per client (act-scope P2).

The materialize_content_items dedup key used to be SCAN-scoped, so every
rescan re-materialized every still-open opportunity as a brand-new card -
the kanban duplicated per rescan (observed: 1146 To-create cards on the
Pierre Fabre client). The handler is now client-scoped ; this script cleans
the historical duplicates.

Per (client_id, content_type, normalized target_question) group :
- keep ONE survivor = most advanced status (published > approved >
  in_review/rejected > draft > generating > identified), tie-break most
  recent created_at ;
- DELETE only duplicate cards still in 'identified' (pure re-materialized
  noise, no generated content, no in-flight job) ;
- duplicates in any other status are KEPT and reported - they may carry
  generated content or user edits, never destroy those.

Cross-scan matching is by normalized question TEXT, never question_id
(rescan copies questions under new ids).

Run (dry run by default) :
    docker exec -e PYTHONPATH=/app senai-worker python scripts/dedupe_content_items.py
    docker exec -e PYTHONPATH=/app senai-worker python scripts/dedupe_content_items.py --apply
    docker exec -e PYTHONPATH=/app senai-worker python scripts/dedupe_content_items.py --client <uuid> --apply
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

_WORKER_DIR = Path(__file__).resolve().parent.parent
if str(_WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKER_DIR))

from sqlalchemy.orm import load_only

from models import Scan, ScanContentItem, SessionLocal

_STATUS_RANK = {
    "identified": 0,
    "generating": 1,
    "draft": 2,
    "in_review": 3,
    "rejected": 3,
    "approved": 4,
    "published": 5,
}


def _normalize_question(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument("--client", help="restrict to one client uuid")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        q = (
            db.query(ScanContentItem, Scan.client_id)
            .join(Scan, Scan.id == ScanContentItem.scan_id)
            .options(load_only(
                ScanContentItem.id, ScanContentItem.scan_id,
                ScanContentItem.content_type, ScanContentItem.target_question,
                ScanContentItem.status, ScanContentItem.created_at,
            ))
        )
        if args.client:
            q = q.filter(Scan.client_id == args.client)
        rows = q.all()

        groups: dict[tuple, list] = defaultdict(list)
        for item, client_id in rows:
            if not item.target_question:
                continue
            key = (str(client_id), item.content_type, _normalize_question(item.target_question))
            groups[key].append(item)

        deleted = 0
        kept_nonidentified_dupes = 0
        dup_groups = 0
        per_client_deleted: dict[str, int] = defaultdict(int)

        for key, items in groups.items():
            if len(items) < 2:
                continue
            dup_groups += 1
            survivor = max(items, key=lambda i: (
                _STATUS_RANK.get(i.status, 0),
                i.created_at or datetime.min,
            ))
            for item in items:
                if item.id == survivor.id:
                    continue
                if item.status == "identified":
                    deleted += 1
                    per_client_deleted[key[0]] += 1
                    if args.apply:
                        db.delete(item)
                else:
                    kept_nonidentified_dupes += 1
                    print(f"  KEEP (status={item.status}) duplicate {item.id} "
                          f"of '{key[2][:60]}' [{key[1]}] client {key[0][:8]}")

        print(f"\n{'APPLY' if args.apply else 'DRY RUN'} : {len(rows)} items scanned, "
              f"{dup_groups} duplicated keys, {deleted} 'identified' duplicates "
              f"{'deleted' if args.apply else 'to delete'}, "
              f"{kept_nonidentified_dupes} non-identified duplicates kept")
        for cid, n in sorted(per_client_deleted.items(), key=lambda kv: -kv[1]):
            print(f"  client {cid}: {n} deleted")

        if args.apply:
            db.commit()
            print("Committed.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
