"""One-shot: align A-Derma scan topic taxonomy to the 4 seo-llm source structure.

Idempotent — safe to re-run. Reads the current scan_topics, applies the changes
below, uses Claude Haiku to redistribute orphaned keywords across the final
4 topics, then recomputes keyword_count.

Mapping decision (validated 2026-05-20):

  Keep + rename / split :
    "Irritation cutanée et démangeaisons"  →  unchanged  (irritation-ade)
    "Cicatrices et vergetures"             →  "Réparation cutanée et cicatrisation"  (réparation-ade)
    "Eczéma et dermatite atopique"         →  SPLIT into 2 :
                                                "Eczéma du corps - Dermatite atopique"  (eczéma-corps-ade)
                                                "Eczéma du visage et paupières"          (eczéma-visage-ade)

  Toggle OFF (kept in DB, won't be scanned) :
    Acné · Rougeurs · Bébé · Grossesse · Solaire · Nettoyage · Marque

  Redistribute via Claude (orphan keywords from deleted/split topics) :
    491 (eczéma) + 267 (hydratation/réparation) ≈ 758 keywords → batched
    into the 4 active topics OR set topic_id=NULL if clearly out of A-Derma's
    seo-llm scope.

Run via :
    SCAN_ID=<aderma-scan-uuid> docker exec senai-api python /tmp/align_topics_aderma.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime

import httpx

sys.path.insert(0, '/app')

from models import SessionLocal, Scan, ScanTopic, ScanKeyword
from config import settings

SCAN_ID = os.environ.get("SCAN_ID", "")

# Target taxonomy — these are the 4 topics that will be ACTIVE after this script.
TARGET_TOPICS = [
    {
        "key": "irritation",
        "name": "Irritation cutanée et démangeaisons",
        "description": "Soulagement des irritations cutanées, démangeaisons (prurit), eczéma de contact, dartres, intertrigos, vulvites — peaux réactives qui grattent ou brûlent.",
    },
    {
        "key": "reparation",
        "name": "Réparation cutanée et cicatrisation",
        "description": "Cicatrisation post-acte dermato/chirurgie, cicatrices hypertrophiques/chéloïdes, vergetures, brûlures, plaies, réparation de la barrière cutanée fragilisée (post-eczéma, post-acné).",
    },
    {
        "key": "eczema_corps",
        "name": "Eczéma du corps - Dermatite atopique",
        "description": "Eczéma localisé au corps (mains, pieds, ventre, plis, dos), dermatite atopique adulte ou enfant, gestion des poussées, hydratation barrière.",
    },
    {
        "key": "eczema_visage",
        "name": "Eczéma du visage et paupières",
        "description": "Eczéma localisé au visage, paupières, contour des yeux, joues — peau fine et sensible.",
    },
]

# Source topics whose keywords need redistribution (in addition to keywords with
# topic_id=NULL after we delete the Eczéma parent).
SOURCE_TOPICS_TO_REDISTRIBUTE = ["Eczéma et dermatite atopique", "Hydratation et réparation cutanée"]
SOURCE_TOPIC_TO_RENAME = ("Cicatrices et vergetures", "Réparation cutanée et cicatrisation")

# Topics to deactivate (kept in DB, is_active=False).
TOPICS_TO_DEACTIVATE = [
    "Acné et peaux acnéiques",
    "Rougeurs et couperose",
    "Soins du bébé et de l'enfant",
    "Santé cutanée grossesse et maternité",
    "Protection solaire peaux fragiles",
    "Nettoyage et hygiène peaux sensibles",
    "Hydratation et réparation cutanée",  # also redistribute its keywords below
    "Marque & navigation",
]

_BATCH = 60
_HAIKU = "claude-haiku-4-5-20251001"


def _build_prompt(keywords: list[str]) -> str:
    topics_block = "\n".join(
        f'- "{t["key"]}" — {t["name"]}: {t["description"]}'
        for t in TARGET_TOPICS
    )
    kws_block = "\n".join(f'{i+1}. {k}' for i, k in enumerate(keywords))
    return f"""You assign each keyword to ONE of the 4 active topics below, or null if it does not fit any.

# Topics (key + description)
{topics_block}
- "null" — keyword is out of A-Derma's scope (cosmetics generic, makeup, hair, oral care, pure hydration unrelated to atopic skin, etc.)

# Keywords to classify
{kws_block}

# Output (JSON only, one entry per keyword in the SAME order, no markdown)
{{
  "assignments": [
    {{"i": 1, "topic": "irritation"}},
    {{"i": 2, "topic": "eczema_visage"}},
    {{"i": 3, "topic": "null"}}
  ]
}}

Be conservative: when in doubt, prefer "null" over a wrong assignment. A keyword like "crème hydratante visage" with no atopic/eczéma signal goes to "null", not "eczema_visage"."""


async def _classify_batch(api_key: str, keywords: list[str]) -> list[str | None]:
    prompt = _build_prompt(keywords)
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": _HAIKU,
                "max_tokens": 4096,
                "temperature": 0.0,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
    text = data["content"][0]["text"]
    from adapters.json_utils import extract_json_object
    parsed = extract_json_object(text)
    by_idx = {entry.get("i"): entry.get("topic") for entry in parsed.get("assignments", [])}
    out: list[str | None] = []
    valid_keys = {t["key"] for t in TARGET_TOPICS}
    for i in range(1, len(keywords) + 1):
        v = by_idx.get(i)
        if v in valid_keys:
            out.append(v)
        else:
            out.append(None)
    return out


def main():
    if not SCAN_ID:
        print("ERROR: SCAN_ID env var required")
        return 1
    if not settings.anthropic_api_key:
        print("ERROR: ANTHROPIC_API_KEY missing in settings")
        return 1

    db = SessionLocal()
    scan = db.query(Scan).filter(Scan.id == SCAN_ID).first()
    if not scan:
        print(f"ERROR: scan {SCAN_ID} not found")
        return 1
    print(f"Scan: {scan.id} ({scan.domain}, status={scan.status})")

    # 1. Snapshot current topics
    topics_by_name = {t.name: t for t in db.query(ScanTopic).filter(ScanTopic.scan_id == SCAN_ID).all()}
    print(f"\nCurrent topics: {len(topics_by_name)}")
    for name, t in topics_by_name.items():
        kw_count = db.query(ScanKeyword).filter(ScanKeyword.topic_id == t.id).count()
        print(f"  {name!r} → {kw_count} keywords")

    # 2. Deactivate the 7 OFF topics (keep their keywords in place)
    deactivated = 0
    for name in TOPICS_TO_DEACTIVATE:
        t = topics_by_name.get(name)
        if t and t.is_active:
            t.is_active = False
            deactivated += 1
    print(f"\n→ Deactivated {deactivated} topics")

    # 3. Rename "Cicatrices et vergetures" → "Réparation cutanée et cicatrisation"
    src, dst = SOURCE_TOPIC_TO_RENAME
    if src in topics_by_name:
        topics_by_name[src].name = dst
        topics_by_name[src].description = next(t["description"] for t in TARGET_TOPICS if t["key"] == "reparation")
        topics_by_name[src].is_active = True
        topics_by_name[dst] = topics_by_name.pop(src)
        print(f"→ Renamed {src!r} → {dst!r}")

    # 4. Build / get the 2 eczéma split topics (corps + visage)
    # Determine display_order base
    max_order = db.query(ScanTopic).filter(ScanTopic.scan_id == SCAN_ID).order_by(ScanTopic.display_order.desc()).first()
    next_order = (max_order.display_order or 0) + 1 if max_order else 1

    def _ensure_target(key: str):
        cfg = next(t for t in TARGET_TOPICS if t["key"] == key)
        existing = topics_by_name.get(cfg["name"])
        if existing:
            existing.is_active = True
            existing.description = cfg["description"]
            return existing
        nonlocal next_order
        new_t = ScanTopic(
            id=uuid.uuid4(),
            scan_id=SCAN_ID,
            name=cfg["name"],
            description=cfg["description"],
            keyword_count=0,
            is_active=True,
            display_order=next_order,
        )
        next_order += 1
        db.add(new_t)
        db.flush()
        topics_by_name[cfg["name"]] = new_t
        return new_t

    target_irritation = _ensure_target("irritation")
    target_reparation = _ensure_target("reparation")
    target_eczema_corps = _ensure_target("eczema_corps")
    target_eczema_visage = _ensure_target("eczema_visage")
    print(f"→ 4 target topics ready (created/reused)")
    key_to_topic_id = {
        "irritation": target_irritation.id,
        "reparation": target_reparation.id,
        "eczema_corps": target_eczema_corps.id,
        "eczema_visage": target_eczema_visage.id,
    }

    # 5. Free up keywords from "Eczéma et dermatite atopique" (delete topic
    # row + set its keywords' topic_id to NULL for redistribution) AND from
    # "Hydratation et réparation cutanée" (idem, but only the SOURCE topic).
    orphan_kws: list[ScanKeyword] = []
    for src_name in SOURCE_TOPICS_TO_REDISTRIBUTE:
        src_topic = topics_by_name.get(src_name)
        if not src_topic:
            continue
        rows = db.query(ScanKeyword).filter(ScanKeyword.topic_id == src_topic.id).all()
        for r in rows:
            r.topic_id = None
            orphan_kws.append(r)
        # Delete the source topic itself (only the eczéma one — the
        # hydratation one we keep but deactivated, so its keywords are
        # already orphaned but its row remains for audit).
        if src_name == "Eczéma et dermatite atopique":
            db.delete(src_topic)
        print(f"→ Freed {len(rows)} keywords from {src_name!r}")
    db.flush()

    # 6. Claude classifies the orphans in batches
    keywords_text = [(kw.id, kw.keyword) for kw in orphan_kws]
    print(f"\nClassifying {len(keywords_text)} orphan keywords via Haiku (batches of {_BATCH})…")
    assignments: dict[str, str | None] = {}
    n_batches = (len(keywords_text) + _BATCH - 1) // _BATCH
    for bi in range(n_batches):
        batch = keywords_text[bi * _BATCH:(bi + 1) * _BATCH]
        try:
            results = asyncio.run(_classify_batch(settings.anthropic_api_key, [k for _, k in batch]))
        except Exception as e:
            print(f"  ✗ batch {bi+1}/{n_batches} failed: {e}")
            continue
        for (kw_id, _), key in zip(batch, results):
            assignments[str(kw_id)] = key
        keys = [r for r in results if r]
        print(f"  ✓ batch {bi+1}/{n_batches}: {len(keys)} assigned, {len(results) - len(keys)} → null")

    # 7. Apply assignments
    assigned = 0
    nulled = 0
    for kw in orphan_kws:
        key = assignments.get(str(kw.id))
        if key:
            kw.topic_id = key_to_topic_id[key]
            assigned += 1
        else:
            kw.topic_id = None  # explicit null = out of scope, won't be scanned
            nulled += 1

    # 8. Recompute keyword_count per topic
    db.flush()
    for name, t in topics_by_name.items():
        count = db.query(ScanKeyword).filter(ScanKeyword.topic_id == t.id).count()
        t.keyword_count = count

    scan.updated_at = datetime.utcnow()
    db.commit()

    # 9. Final report
    print(f"\n=========================")
    print(f"SUMMARY (align_topics A-Derma)")
    print(f"  topics deactivated   : {deactivated}")
    print(f"  orphan keywords      : {len(orphan_kws)}")
    print(f"  assigned to a topic  : {assigned}")
    print(f"  set to null (orphan) : {nulled}")
    print(f"\nFinal active topics:")
    for t in db.query(ScanTopic).filter(ScanTopic.scan_id == SCAN_ID, ScanTopic.is_active == True).order_by(ScanTopic.display_order).all():
        print(f"  {t.name!r} → {t.keyword_count} keywords")
    print(f"=========================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
