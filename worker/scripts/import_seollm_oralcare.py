"""One-shot import of ONE Pierre Fabre Oral Care brand's seo-llm personas +
questions into its sen-ai scan (Option B — one scan per brand).

Run:
    SCAN_ID=<uuid> BRAND=elg SEOLLM_CACHE=/tmp/seollm_oralcare_elg \
      docker exec senai-worker python /tmp/import_seollm_oralcare.py
  (BRAND = elg | ina | art | elu)

Cache must contain personas_<slug>_03122025_3.json + questions_<slug>_03122025_3_5.json
for the brand's slugs (e.g. for elg: bebe-elg, blancheur-elg, …).
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, '/app')

from models import SessionLocal, Scan, ScanTopic, ScanPersona, ScanQuestion, Job
from oralcare_brands import get_brand

SCAN_ID = os.environ.get("SCAN_ID", "")
BRAND = os.environ.get("BRAND", "")
SEOLLM_CACHE = Path(os.environ.get("SEOLLM_CACHE", ""))


def _candidate_question_paths(slug):
    return [SEOLLM_CACHE / f"questions_{slug}_03122025_3_5.json",
            SEOLLM_CACHE / f"questions_{slug}_03122025_3_15.json"]


def find_topic(topics, prefix):
    for t in topics:
        if t.is_active and t.name.startswith(prefix):
            return t
    return None


def load_cache(slug):
    p = SEOLLM_CACHE / f"personas_{slug}_03122025_3.json"
    personas = json.loads(p.read_text(encoding="utf-8"))["personas"]
    questions = []
    for qp in _candidate_question_paths(slug):
        if qp.exists():
            questions = json.loads(qp.read_text(encoding="utf-8"))["questions"]
            break
    if not questions:
        raise FileNotFoundError(f"No questions file for slug '{slug}' under {SEOLLM_CACHE}")
    return personas, questions


def main():
    if not SCAN_ID or not BRAND or not str(SEOLLM_CACHE):
        print("ERROR: SCAN_ID, BRAND, SEOLLM_CACHE env vars required")
        return 1
    cfg = get_brand(BRAND)
    topic_mapping = cfg["slug_to_prefix"]

    db = SessionLocal()
    scan = db.query(Scan).filter(Scan.id == SCAN_ID).first()
    if not scan:
        print(f"ERROR: scan {SCAN_ID} not found")
        return 1
    print(f"Scan: {scan.id} ({scan.domain}, status={scan.status}) — brand={cfg['brand_name']}")

    topics = db.query(ScanTopic).filter(ScanTopic.scan_id == SCAN_ID).all()
    resolved = {}
    for slug, prefix in topic_mapping.items():
        t = find_topic(topics, prefix)
        if not t:
            print(f"  ⚠ NO MATCH for '{slug}' (prefix='{prefix}')")
            continue
        resolved[slug] = t
        print(f"  ✓ {slug} → {t.name}")

    if len(resolved) != len(topic_mapping):
        print(f"\nABORT: only {len(resolved)}/{len(topic_mapping)} topics resolved. Active topics:")
        for t in topics:
            if t.is_active:
                print(f"  - {t.name!r}")
        return 1

    total_p = total_q = skipped = 0
    for slug, topic in resolved.items():
        try:
            personas, questions = load_cache(slug)
        except FileNotFoundError as e:
            print(f"  ✗ {e}")
            return 1
        deleted = db.query(ScanPersona).filter(
            ScanPersona.scan_id == SCAN_ID, ScanPersona.topic_id == topic.id
        ).delete(synchronize_session=False)
        db.flush()
        print(f"\n[{slug}] → '{topic.name}'  (deleted {deleted} auto-gen)")
        q_by_persona = {}
        for q in questions:
            q_by_persona.setdefault(q.get("persona_nom"), []).append(q)
        for p in personas:
            nom = p.get("nom") or "Unknown"
            pq = q_by_persona.get(nom, [])
            data = dict(p)
            data["questions"] = [{"type_question": q.get("type_question"), "question": q.get("question"),
                                  "intention_cachee": q.get("intention_cachee"), "signal_positif": q.get("signal_positif"),
                                  "signal_negatif": q.get("signal_negatif")} for q in pq]
            data["_source"] = {"origin": "seo-llm", "csv_slug": slug, "brand": BRAND}
            np = ScanPersona(id=uuid.uuid4(), scan_id=SCAN_ID, topic_id=topic.id, name=nom, data=data, is_active=True)
            db.add(np); db.flush(); total_p += 1
            for q in pq:
                text = (q.get("question") or "").strip()
                if len(text) < 10:
                    skipped += 1; continue
                db.add(ScanQuestion(id=uuid.uuid4(), scan_id=SCAN_ID, persona_id=np.id, question=text,
                                    type_question=q.get("type_question") or "basique", is_active=True, intent_category=None))
                total_q += 1
        print(f"  inserted {len(personas)} personas, {sum(len(v) for v in q_by_persona.values())} questions")

    c = scan.config or {}
    c.update({"import_origin": "seo-llm", "import_brand": BRAND, "import_brand_name": cfg["brand_name"],
              "import_source_ids": sorted(cfg["source_ids"]), "import_timestamp": datetime.utcnow().isoformat(),
              "credits_already_debited": True})
    scan.config = c
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(scan, "config")
    scan.updated_at = datetime.utcnow()
    db.add(Job(scan_id=SCAN_ID, client_id=scan.client_id, job_type="classify_question_intent", status="pending", payload={}))
    db.commit()

    print(f"\n=== SUMMARY import Oral Care / {cfg['brand_name']} ===")
    print(f"  topics replaced={len(resolved)}  personas={total_p}  questions={total_q}  skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
