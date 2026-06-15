"""Re-import Avene seo-llm history as SELF-CONTAINED child scans.

Replaces the 4 existing Avene import children (which pointed results at the
ROOT scan's questions by text-match and lost 4 of 7 topics to text drift)
with self-contained children that carry their own topics/personas/questions
reconstructed from the seo-llm originals. See prep_avene_selfcontained.py for
the why + the data-driven topic mapping.

Reads enriched JSONL bundles (one per day) produced by the prep script. Each
record has: day, execution_timestamp, topic, persona_name, question_text,
question_type, intent_category, provider, model, response_text, citations,
brand_mentions, brand_analysis, target_*, *_tokens.

Idempotent-ish: it first DELETES every existing child of the root whose
config.import_origin = 'seo-llm-history', then recreates. Rollback is the same
delete. Antedates created_at/completed_at to the capture day (lineage sorts on
created_at), bumps the root run_index to N+1.

Run inside the api/worker container:
  docker cp avene_selfcontained_bundle/. senai-api:/tmp/avene_sc/
  docker cp import_avene_selfcontained.py senai-api:/tmp/
  docker exec -e SCAN_ID=<root> -e BUNDLE_DIR=/tmp/avene_sc [-e DRY_RUN=1] \
      -e PYTHONPATH=/app senai-api python /tmp/import_avene_selfcontained.py
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

from models import (
    SessionLocal, Scan, ScanTopic, ScanPersona, ScanQuestion, ScanLLMResult,
)

SCAN_ID = os.environ["SCAN_ID"]
BUNDLE_DIR = Path(os.environ["BUNDLE_DIR"])
DRY_RUN = os.environ.get("DRY_RUN", "") not in ("", "0", "false")


def norm(s):
    return re.sub(r"\s+", " ", str(s or "").strip()).lower()


def main():
    db = SessionLocal()
    root = db.query(Scan).filter(Scan.id == SCAN_ID).first()
    if not root:
        print("ERROR: root %s not found" % SCAN_ID)
        return 1
    print("root: %s (%s) run_index=%s" % (root.id, root.domain, root.run_index))

    # --- delete existing import children (and cascade their rows) ---
    old = db.query(Scan).filter(
        Scan.parent_scan_id == root.id,
        Scan.config["import_origin"].astext == "seo-llm-history",
    ).all()
    print("existing import children to delete: %d" % len(old))
    for c in old:
        n_res = db.query(ScanLLMResult).filter(ScanLLMResult.scan_id == c.id).count()
        print("  %s %s results=%d" % (str(c.id)[:8], (c.config or {}).get("import_period"), n_res))
        if not DRY_RUN:
            db.delete(c)  # cascade deletes results/topics/personas/questions
    if not DRY_RUN:
        db.flush()

    days = sorted(p.stem for p in BUNDLE_DIR.glob("*.jsonl"))
    if not days:
        print("ERROR: no bundles in %s" % BUNDLE_DIR)
        return 1
    print("bundle days: %s" % days)

    created = 0
    total_results = 0
    for di, day in enumerate(days):
        records = [json.loads(l) for l in (BUNDLE_DIR / ("%s.jsonl" % day)).open(encoding="utf-8")]
        ts = [r["execution_timestamp"] for r in records if r.get("execution_timestamp")]
        completed_at = datetime.fromisoformat(max(ts)) if ts else datetime.fromisoformat(day + "T12:00:00")
        started_at = datetime.fromisoformat(min(ts)) if ts else completed_at

        child = Scan(
            id=uuid.uuid4(),
            client_id=root.client_id,
            name="%s - %s" % (root.name, day),
            domain=root.domain,
            status="completed",
            focus_brand_id=root.focus_brand_id,
            promotion_brand_ids=root.promotion_brand_ids,
            scan_type=root.scan_type,
            parent_scan_id=root.id,
            schedule="manual",
            run_index=di + 1,
            config={
                "import_origin": "seo-llm-history",
                "import_period": day,
                "import_style": "self-contained",
                "credits_already_debited": True,
            },
            progress_pct=100,
            summary=None,
            created_by=root.created_by,
            created_at=started_at,
            updated_at=completed_at,
            started_at=started_at,
            completed_at=completed_at,
        )

        # Build topics / personas / questions for THIS child.
        topic_rows = {}      # topic_name -> ScanTopic
        persona_rows = {}    # (topic_name, persona_name) -> ScanPersona
        question_rows = {}   # (topic_name, persona_name, qnorm) -> ScanQuestion
        result_rows = []
        order = 0
        for r in records:
            tname = r.get("topic") or "Other"
            pname = r.get("persona_name") or "?"
            qtext = (r.get("question_text") or "").strip()
            if not qtext:
                continue
            if tname not in topic_rows:
                order += 1
                topic_rows[tname] = ScanTopic(
                    id=uuid.uuid4(), scan_id=child.id, name=tname,
                    is_active=True, display_order=order,
                )
            pkey = (tname, pname)
            if pkey not in persona_rows:
                persona_rows[pkey] = ScanPersona(
                    id=uuid.uuid4(), scan_id=child.id, topic_id=topic_rows[tname].id,
                    name=pname, data={"_import_origin": "seo-llm-history"}, is_active=True,
                )
            qkey = (tname, pname, norm(qtext))
            if qkey not in question_rows:
                question_rows[qkey] = ScanQuestion(
                    id=uuid.uuid4(), scan_id=child.id, persona_id=persona_rows[pkey].id,
                    question=qtext, type_question=r.get("question_type"),
                    intent_category=r.get("intent_category"), is_active=True,
                    fan_out_queries=[],
                )
            qid = question_rows[qkey].id
            result_rows.append(ScanLLMResult(
                id=uuid.uuid4(), scan_id=child.id, question_id=qid,
                provider=r["provider"], model=r.get("model"),
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

        print("[%s] %d records -> %d topics, %d personas, %d questions, %d results" % (
            day, len(records), len(topic_rows), len(persona_rows), len(question_rows), len(result_rows)))

        if not DRY_RUN:
            db.add(child)
            db.flush()
            db.bulk_save_objects(list(topic_rows.values()))
            db.bulk_save_objects(list(persona_rows.values()))
            db.bulk_save_objects(list(question_rows.values()))
            db.bulk_save_objects(result_rows)
        created += 1
        total_results += len(result_rows)

    if not DRY_RUN and created:
        root.run_index = created + 1
        root.updated_at = datetime.utcnow()
        db.commit()
        print("COMMITTED. root run_index -> %d" % (created + 1))
    else:
        db.rollback()
        print("DRY RUN - rolled back")
    print("children created: %d, results: %d" % (created, total_results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
