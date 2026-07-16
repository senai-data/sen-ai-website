"""Backfill scan.summary["models"] for existing completed scans (P3 model eras).

Uses the EXACT same derivation as the live completion path
(handlers.run_llm_tests.derive_scan_models) so historical points and future
points can never disagree on deploy day: {provider: most-frequent non-NULL
model} from the scan's own scan_llm_results rows, run_index>=1, consensus
meta-rows excluded.

The reserved "analyzer" key is stamped on NATIVE scans only, with the value
that has been the constant since the analyzer existed (gemini-2.5-flash-lite).
Imported scans (config.import_period) ran under a different analyzer entirely
and are already covered by the sentiment import mask - stamping them would
fabricate a false "same instrument" claim, so they get provider entries only.
Native scans where the analyzer happened to be skipped can't be told apart
historically; stamping them anyway is the documented approximation.

Read-modify-write on summary (dict copy + flag_modified) - NEVER a wholesale
replace: generate_editorial / notify-complete / audit handlers keep mutating
summaries after completion. Re-runnable: scans whose summary already has a
"models" key are skipped.

Run (dry-run by default):
    docker exec senai-worker python scripts/backfill_summary_models.py
    docker exec senai-worker python scripts/backfill_summary_models.py --apply
    docker exec senai-worker python scripts/backfill_summary_models.py --client <uuid>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_WORKER_DIR = Path(__file__).resolve().parent.parent
if str(_WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKER_DIR))

from sqlalchemy.orm.attributes import flag_modified

from handlers.run_llm_tests import derive_scan_models
from models import Scan, SessionLocal

# The analyzer model has been this constant since the config key exists
# (worker/config.py model_brand_analyzer) - safe stamp for native history.
HISTORICAL_ANALYZER_MODEL = "gemini-2.5-flash-lite"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument("--client", help="restrict to one client uuid")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        q = db.query(Scan).filter(Scan.status == "completed")
        if args.client:
            q = q.filter(Scan.client_id == args.client)
        scans = q.order_by(Scan.client_id, Scan.created_at).all()

        stamped = 0
        skipped_present = 0
        skipped_empty = 0
        for scan in scans:
            summary = dict(scan.summary or {})
            if "models" in summary:
                skipped_present += 1
                continue

            models = derive_scan_models(db, scan.id)
            imported = bool((scan.config or {}).get("import_period"))
            if models and not imported:
                models["analyzer"] = HISTORICAL_ANALYZER_MODEL
            if not models:
                # Nothing derivable (e.g. all-NULL imported rows) - leave the
                # key absent, trend consumers treat it as unknown (no boundary).
                skipped_empty += 1
                print(f"  SKIP empty  {scan.id} client={scan.client_id} run={scan.run_index} ({scan.name})")
                continue

            tag = "import" if imported else "native"
            print(f"  {'APPLY' if args.apply else 'DRY  '} {tag:6} {scan.id} client={scan.client_id} "
                  f"run={scan.run_index} models={models}")
            if args.apply:
                summary["models"] = models
                scan.summary = summary
                flag_modified(scan, "summary")
            stamped += 1

        if args.apply:
            db.commit()
        print(f"\n{'Applied' if args.apply else 'Dry run'}: {stamped} stamped, "
              f"{skipped_present} already had models, {skipped_empty} not derivable "
              f"(total completed scans: {len(scans)})")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
