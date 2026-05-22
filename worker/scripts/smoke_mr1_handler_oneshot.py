"""One-shot smoke for Phase MR.1 handler — runs against the live DB.

Executes the full aggregate + upsert path (no LinkFinder by default).
Reports counts. Then DELETEs only rows it just touched IF --cleanup is set.
By default, leaves the upserted rows in place — they're real catalog data.

Usage inside senai-worker container :
    docker exec senai-worker python /app/scripts/smoke_mr1_handler_oneshot.py
    docker exec senai-worker python /app/scripts/smoke_mr1_handler_oneshot.py --enrich
    docker exec senai-worker python /app/scripts/smoke_mr1_handler_oneshot.py --cleanup
"""

from __future__ import annotations

import argparse
import json
import sys

from models import SessionLocal
from services.media_catalog_io import (
    aggregate_citations,
    collect_filtered_domains,
    enrich_with_linkfinder,
    upsert_catalog_rows,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--enrich", action="store_true", help="Also call LinkFinder")
    parser.add_argument("--cleanup", action="store_true", help="DELETE all media_catalog rows after the run")
    parser.add_argument("--dry", action="store_true", help="Stop after aggregation, do not upsert")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        excluded = collect_filtered_domains(db)
        print(f"[1/4] Exclusion set: {len(excluded)} domains")

        buckets = aggregate_citations(db, excluded_domains=excluded)
        print(f"[2/4] Aggregated: {len(buckets)} (domain, country, language) buckets")

        if buckets:
            preview_keys = list(buckets.keys())[:5]
            print("       Preview:")
            for k in preview_keys:
                b = buckets[k]
                print(f"         {k}: count={b['llm_citation_count']}, "
                      f"decayed={b['llm_citation_decayed']:.2f}, "
                      f"topics={b['topic_areas'][:3]}, "
                      f"vertical={b['vertical'][:2]}")

        if args.dry:
            print("[3/4] --dry: skipping upsert")
            return 0

        inserted, updated = upsert_catalog_rows(db, buckets)
        print(f"[3/4] Upsert: inserted={inserted}, updated={updated}")

        if args.enrich:
            stats = enrich_with_linkfinder(db, max_domains=50)
            print(f"[4/4] LinkFinder enrich: {json.dumps(stats)}")
        else:
            print("[4/4] LinkFinder enrich SKIPPED (pass --enrich to run)")

        if args.cleanup:
            from sqlalchemy import text
            n = db.execute(text("DELETE FROM media_catalog")).rowcount
            db.commit()
            print(f"[cleanup] DELETE FROM media_catalog → {n} rows")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
