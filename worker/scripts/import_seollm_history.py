"""Import seo-llm historical runs as child scans of an existing sen-ai scan.

Reads the JSONL bundles produced by prep_seollm_history.py (one file per
capture-day) and creates, for each day :

  - a child Scan (parent_scan_id = the root scan, status=completed,
    created_at/completed_at ANTEDATED to the capture day - critical, the
    lineage resolution in /results/aggregated sorts by created_at)
  - one ScanLLMResult per record, with citations[] / brand_mentions[] /
    brand_analysis reconstructed from the seo-llm dimensional facts.
    question_id points to the ROOT scan's ScanQuestion (matched on
    normalized persona_name + question_text) - the aggregated endpoint
    groups across the lineage by question text so no question copies
    are needed.

Idempotent : a child scan whose config->>'import_period' matches the day
is skipped entirely. Roll back any brand with :
  DELETE FROM scans WHERE parent_scan_id='<root>' AND config->>'import_origin'='seo-llm-history';

After the children are inserted, the root scan's run_index is bumped to
(number of historical runs + 1) so the series reads 1..N chronologically.

Run inside the api container :
  docker cp bundle/. senai-api:/tmp/seollm_history/
  docker cp import_seollm_history.py senai-api:/tmp/
  docker exec -e SCAN_ID=<root-uuid> -e BUNDLE_DIR=/tmp/seollm_history/<source_domain> \
      [-e DRY_RUN=1] senai-api python /tmp/import_seollm_history.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/app")

from models import SessionLocal, Scan, ScanPersona, ScanQuestion, ScanLLMResult

SCAN_ID = os.environ["SCAN_ID"]
BUNDLE_DIR = Path(os.environ["BUNDLE_DIR"])
DRY_RUN = os.environ.get("DRY_RUN", "") not in ("", "0", "false")


def norm(s: str | None) -> str:
    """Whitespace-collapsed, lowered text for matching."""
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def main() -> int:
    db = SessionLocal()
    root = db.query(Scan).filter(Scan.id == SCAN_ID).first()
    if not root:
        print(f"ERROR: scan {SCAN_ID} not found")
        return 1
    print(f"root scan : {root.id} ({root.domain}) status={root.status} run_index={root.run_index}")

    # --- question matching table -------------------------------------------
    personas = {str(p.id): p for p in db.query(ScanPersona).filter(ScanPersona.scan_id == SCAN_ID).all()}
    questions = db.query(ScanQuestion).filter(ScanQuestion.scan_id == SCAN_ID).all()
    qlookup: dict[tuple[str, str], uuid.UUID] = {}
    for q in questions:
        p = personas.get(str(q.persona_id))
        qlookup[(norm(p.name if p else ""), norm(q.question))] = q.id
    # Question-text-only fallback : personas were re-imported and a few
    # names may have drifted ; the question text is the strong key.
    qlookup_text: dict[str, uuid.UUID] = {}
    for q in questions:
        qlookup_text.setdefault(norm(q.question), q.id)
    print(f"root scan has {len(questions)} questions, {len(personas)} personas")

    # --- existing children (idempotency) ------------------------------------
    existing_periods = {
        (s.config or {}).get("import_period")
        for s in db.query(Scan).filter(Scan.parent_scan_id == SCAN_ID).all()
    }

    days = sorted(p.stem for p in BUNDLE_DIR.glob("*.jsonl"))
    if not days:
        print(f"ERROR: no JSONL bundles in {BUNDLE_DIR}")
        return 1
    print(f"bundle days : {days}")

    total_inserted = 0
    total_unmatched = 0
    created_children = 0

    for di, day in enumerate(days):
        if day in existing_periods:
            print(f"[{day}] child scan already exists - skipped")
            continue

        records = [json.loads(line) for line in (BUNDLE_DIR / f"{day}.jsonl").open(encoding="utf-8")]
        ts = [r["execution_timestamp"] for r in records if r.get("execution_timestamp")]
        completed_at = datetime.fromisoformat(max(ts)) if ts else datetime.fromisoformat(day + "T12:00:00")
        started_at = datetime.fromisoformat(min(ts)) if ts else completed_at

        child = Scan(
            id=uuid.uuid4(),
            client_id=root.client_id,
            name=f"{root.name} - {day}",
            domain=root.domain,
            status="completed",
            focus_brand_id=root.focus_brand_id,
            promotion_brand_ids=root.promotion_brand_ids,
            scan_type=root.scan_type,
            parent_scan_id=root.id,
            schedule="manual",
            run_index=di + 1,  # chronological 1..N, root bumped to N+1 below
            config={
                "import_origin": "seo-llm-history",
                "import_period": day,
                "credits_already_debited": True,
            },
            progress_pct=100,
            summary=None,
            created_by=root.created_by,
            created_at=started_at,   # ANTEDATED - lineage sorts on created_at
            updated_at=completed_at,
            started_at=started_at,
            completed_at=completed_at,
        )

        matched = 0
        unmatched = 0
        rows = []
        for r in records:
            qid = qlookup.get((norm(r["persona_name"]), norm(r["question_text"]))) \
                or qlookup_text.get(norm(r["question_text"]))
            if qid is None:
                unmatched += 1
                continue
            rows.append(ScanLLMResult(
                id=uuid.uuid4(),
                scan_id=child.id,
                question_id=qid,
                provider=r["provider"],
                model=r.get("model"),
                response_text=r.get("response_text"),
                citations=r.get("citations") or [],
                target_cited=bool(r.get("target_cited")),
                target_position=r.get("target_position"),
                total_citations=r.get("total_citations"),
                competitor_domains=None,
                brand_mentions=r.get("brand_mentions") or [],
                brand_analysis=r.get("brand_analysis") or {},
                duration_ms=r.get("duration_ms"),
                input_tokens=r.get("input_tokens"),
                output_tokens=r.get("output_tokens"),
                created_at=datetime.fromisoformat(r["execution_timestamp"]) if r.get("execution_timestamp") else completed_at,
                web_search_queries=[],
                run_index=1,
            ))
            matched += 1

        match_rate = matched / len(records) * 100 if records else 0
        print(f"[{day}] {len(records)} records -> {matched} matched ({match_rate:.0f}%), {unmatched} unmatched")
        if match_rate < 50:
            print(f"[{day}] ABORT for this day : match rate below 50%, mapping looks broken")
            continue

        if not DRY_RUN:
            db.add(child)
            db.flush()
            db.bulk_save_objects(rows)
        created_children += 1
        total_inserted += matched
        total_unmatched += unmatched

    if not DRY_RUN and created_children:
        # The root is the latest run chronologically (May 19) -> N+1.
        root.run_index = created_children + 1
        root.updated_at = datetime.utcnow()
        db.commit()
    elif DRY_RUN:
        db.rollback()

    print("\n=========================")
    print(f"{'DRY RUN - nothing written' if DRY_RUN else 'COMMITTED'}")
    print(f"  child scans created : {created_children}")
    print(f"  results inserted    : {total_inserted}")
    print(f"  records unmatched   : {total_unmatched}")
    if not DRY_RUN and created_children:
        print(f"  root run_index bumped to {created_children + 1}")
    print("=========================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
