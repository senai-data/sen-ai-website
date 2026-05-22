"""One-shot import of seo-llm Ducray personas + questions into a sen-ai scan.

Twin of import_seollm_avene.py / import_seollm_aderma.py.

  - 6 source CSVs (acné-duc, anaphase-duc, dexyane-duc,
    hyperpigmentation-duc, pellicule-duc, prurit-duc)
  - 32 personas total, 468 questions total per SharePoint dim_persona snapshot

Run via:
    SCAN_ID=<the-ducray-scan-uuid> \
      SEOLLM_CACHE=/tmp/seollm_ducray \
      docker exec senai-worker python /tmp/import_seollm_ducray.py
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from datetime import datetime

sys.path.insert(0, '/app')

from models import SessionLocal, Scan, ScanTopic, ScanPersona, ScanQuestion, Job

SCAN_ID = os.environ.get("SCAN_ID", "")
SEOLLM_CACHE = Path(os.environ.get("SEOLLM_CACHE", "/tmp/seollm_ducray"))

# seo-llm slug → sen-ai topic name prefix. Adjust the right-hand strings to
# match what sen-ai actually generated for this scan (case-sensitive prefix).
# These prefixes are best-guesses based on Ducray's product taxonomy — finalise
# after running align_topics_ducray.py which renames topics to known values.
TOPIC_MAPPING = {
    "acné-duc":              "Acné",
    "anaphase-duc":          "Chute de cheveux",
    "dexyane-duc":           "Eczéma",
    "hyperpigmentation-duc": "Hyperpigmentation",
    "pellicule-duc":         "Pellicules",
    "prurit-duc":            "Prurit",
}

# Local cache files are 3-persona variants; the per-file content can hold
# 5-6 personas regardless (the suffix is a generation seed, not a hard cap).
PERSONAS_PER_SOURCE = {slug: 3 for slug in TOPIC_MAPPING}

DUCRAY_SOURCE_IDS = [1, 2, 13, 17, 19, 23]


def _candidate_question_paths(slug: str, nb_personas: int) -> list[Path]:
    return [
        SEOLLM_CACHE / f"questions_{slug}_03122025_{nb_personas}_15.json",
        SEOLLM_CACHE / f"questions_{slug}_03122025_{nb_personas}_5.json",
    ]


def find_topic(topics, prefix):
    for t in topics:
        if t.name.startswith(prefix):
            return t
    return None


def load_cache(slug):
    n = PERSONAS_PER_SOURCE.get(slug, 3)
    p = SEOLLM_CACHE / f"personas_{slug}_03122025_{n}.json"
    personas = json.loads(p.read_text(encoding="utf-8"))["personas"]
    questions = []
    for qpath in _candidate_question_paths(slug, n):
        if qpath.exists():
            questions = json.loads(qpath.read_text(encoding="utf-8"))["questions"]
            break
    if not questions:
        raise FileNotFoundError(
            f"No questions file for slug '{slug}'. Tried: "
            f"{[str(p) for p in _candidate_question_paths(slug, n)]}"
        )
    return personas, questions


def main():
    if not SCAN_ID:
        print("ERROR: SCAN_ID env var required")
        return 1
    db = SessionLocal()
    scan = db.query(Scan).filter(Scan.id == SCAN_ID).first()
    if not scan:
        print(f"ERROR: scan {SCAN_ID} not found")
        return 1
    print(f"Scan: {scan.id} ({scan.domain}, status={scan.status})")

    topics = db.query(ScanTopic).filter(ScanTopic.scan_id == SCAN_ID).all()
    print(f"Scan has {len(topics)} topics in sen-ai")

    resolved = {}
    for slug, prefix in TOPIC_MAPPING.items():
        t = find_topic(topics, prefix)
        if not t:
            print(f"  ⚠ NO MATCH for '{slug}' (prefix='{prefix}') — will skip")
            continue
        resolved[slug] = t
        print(f"  ✓ {slug} → {t.name}")

    if len(resolved) != len(TOPIC_MAPPING):
        print(f"\nABORT: only {len(resolved)}/{len(TOPIC_MAPPING)} topics resolved.")
        print("Fix TOPIC_MAPPING prefixes (top of script) and re-run.")
        print("Available topics:")
        for t in topics:
            print(f"  - {t.name!r}")
        return 1

    total_personas = total_questions = skipped_questions = 0

    for slug, topic in resolved.items():
        try:
            personas, questions = load_cache(slug)
        except FileNotFoundError as e:
            print(f"  ✗ cache missing for '{slug}': {e}")
            return 1

        deleted_p = db.query(ScanPersona).filter(
            ScanPersona.scan_id == SCAN_ID,
            ScanPersona.topic_id == topic.id,
        ).delete(synchronize_session=False)
        db.flush()
        print(f"\n[{slug}] → '{topic.name}'")
        print(f"  deleted {deleted_p} auto-gen personas (questions cascaded)")

        q_by_persona = {}
        for q in questions:
            q_by_persona.setdefault(q.get("persona_nom"), []).append(q)

        for p in personas:
            nom = p.get("nom") or "Unknown"
            p_questions = q_by_persona.get(nom, [])
            data = dict(p)
            data["questions"] = [
                {
                    "type_question": q.get("type_question"),
                    "question": q.get("question"),
                    "intention_cachee": q.get("intention_cachee"),
                    "signal_positif": q.get("signal_positif"),
                    "signal_negatif": q.get("signal_negatif"),
                }
                for q in p_questions
            ]
            data["_source"] = {"origin": "seo-llm", "csv_slug": slug, "brand": "ducray"}

            new_persona = ScanPersona(
                id=uuid.uuid4(),
                scan_id=SCAN_ID,
                topic_id=topic.id,
                name=nom,
                data=data,
                is_active=True,
            )
            db.add(new_persona)
            db.flush()
            total_personas += 1

            for q in p_questions:
                text = (q.get("question") or "").strip()
                if len(text) < 10:
                    skipped_questions += 1
                    continue
                db.add(ScanQuestion(
                    id=uuid.uuid4(),
                    scan_id=SCAN_ID,
                    persona_id=new_persona.id,
                    question=text,
                    type_question=q.get("type_question") or "basique",
                    is_active=True,
                    intent_category=None,
                ))
                total_questions += 1

        print(f"  inserted {len(personas)} personas, "
              f"{sum(len(qs) for qs in q_by_persona.values())} questions")

    cfg = scan.config or {}
    cfg["import_origin"] = "seo-llm"
    cfg["import_source_ids"] = sorted(DUCRAY_SOURCE_IDS)
    cfg["import_brand"] = "ducray"
    cfg["import_timestamp"] = datetime.utcnow().isoformat()
    cfg["credits_already_debited"] = True
    scan.config = cfg
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(scan, "config")
    scan.updated_at = datetime.utcnow()

    job = Job(
        scan_id=SCAN_ID,
        client_id=scan.client_id,
        job_type="classify_question_intent",
        status="pending",
        payload={},
    )
    db.add(job)

    db.commit()

    print(f"\n=========================")
    print(f"SUMMARY (Ducray import)")
    print(f"  topics replaced  : {len(resolved)} / {len(topics)} total")
    print(f"  personas inserted: {total_personas}")
    print(f"  questions inserted: {total_questions}")
    print(f"  questions skipped (<10 chars): {skipped_questions}")
    print(f"  classify_question_intent job enqueued: {job.id}")
    print(f"=========================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
