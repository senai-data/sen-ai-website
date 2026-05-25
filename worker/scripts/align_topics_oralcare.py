"""One-shot: align ONE Pierre Fabre Oral Care brand's scan to its seo-llm topics
(Option B — one scan per brand, all on pierrefabre-oralcare.com).

Fresh-create strategy: deactivate every auto-detected topic, create only THIS
brand's target topics, pool all keywords to NULL, let Haiku redistribute (the
site's non-brand keywords fall to null, which is correct — this scan focuses on
the brand's categories).

Run:
    SCAN_ID=<uuid> BRAND=elg \
      docker exec senai-worker python /tmp/align_topics_oralcare.py
  (BRAND = elg | ina | art | elu)
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # import oralcare_brands from same dir
sys.path.insert(0, '/app')

from models import SessionLocal, Scan, ScanTopic, ScanKeyword
from config import settings
from adapters.json_utils import extract_json_object
from oralcare_brands import get_brand

SCAN_ID = os.environ.get("SCAN_ID", "")
BRAND = os.environ.get("BRAND", "")
_BATCH = 60
_HAIKU = "claude-haiku-4-5-20251001"


def _build_prompt(topics, keywords):
    topics_block = "\n".join(f'- "{t.id}" — {t.name}: {t.description or ""}' for t in topics)
    kws_block = "\n".join(f'{i+1}. {k}' for i, k in enumerate(keywords))
    return f"""Assign each keyword to ONE topic by its UUID, or "null" if it doesn't fit any.
All topics belong to the oral-care / dental-hygiene vertical.

# Active topics
{topics_block}
- "null" — keyword is out of scope (not this brand's categories, generic, ambiguous).

# Keywords
{kws_block}

# Output (JSON only, same order)
{{"assignments": [{{"i": 1, "topic": "<topic-uuid or 'null'>"}}]}}

Conservative: when in doubt → "null"."""


async def _call(api_key, prompt):
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": _HAIKU, "max_tokens": 4096, "temperature": 0.0,
                  "messages": [{"role": "user", "content": prompt}]},
        )
        resp.raise_for_status()
        return resp.json()


def main():
    if not SCAN_ID or not BRAND:
        print("ERROR: SCAN_ID and BRAND env vars required")
        return 1
    if not settings.anthropic_api_key:
        print("ERROR: ANTHROPIC_API_KEY missing")
        return 1
    cfg = get_brand(BRAND)
    targets = cfg["topics"]

    db = SessionLocal()
    scan = db.query(Scan).filter(Scan.id == SCAN_ID).first()
    if not scan:
        print(f"ERROR: scan {SCAN_ID} not found")
        return 1
    print(f"Scan: {scan.id} ({scan.domain}) — brand={cfg['brand_name']} ({len(targets)} target topics)")

    # 1. Deactivate ALL current topics.
    current = db.query(ScanTopic).filter(ScanTopic.scan_id == SCAN_ID).all()
    deactivated = 0
    for t in current:
        if t.is_active:
            t.is_active = False
            deactivated += 1
    db.flush()
    print(f"Deactivated {deactivated} auto-detected topics")

    # 2. Create the brand's target topics.
    max_order = (db.query(ScanTopic.display_order).filter(ScanTopic.scan_id == SCAN_ID)
                 .order_by(ScanTopic.display_order.desc()).first())
    next_order = (max_order[0] or 0) + 1 if max_order else 1
    key_to_id = {}
    for tcfg in targets:
        t = ScanTopic(id=uuid.uuid4(), scan_id=SCAN_ID, name=tcfg["name"],
                      description=tcfg["description"], keyword_count=0, is_active=True,
                      display_order=next_order)
        next_order += 1
        db.add(t); db.flush()
        key_to_id[tcfg["key"]] = str(t.id)
        print(f"  + {tcfg['name']!r}")
    db.flush()

    # 3. Pool every keyword to NULL.
    all_kws = db.query(ScanKeyword).filter(ScanKeyword.scan_id == SCAN_ID).all()
    for kw in all_kws:
        kw.topic_id = None
    db.flush()
    print(f"Keywords pooled: {len(all_kws)}")

    # 4. Haiku assigns across the brand's topics.
    valid = set(key_to_id.values())
    active = [t for t in db.query(ScanTopic).filter(ScanTopic.scan_id == SCAN_ID, ScanTopic.is_active == True)
              .order_by(ScanTopic.display_order).all() if str(t.id) in valid]
    assigned = nulled = 0
    n_batches = (len(all_kws) + _BATCH - 1) // _BATCH
    print(f"Classifying {len(all_kws)} keywords via Haiku ({n_batches} batches)…")
    for bi in range(n_batches):
        batch = all_kws[bi * _BATCH:(bi + 1) * _BATCH]
        try:
            resp = asyncio.run(_call(settings.anthropic_api_key, _build_prompt(active, [k.keyword for k in batch])))
            by_idx = {e.get("i"): e.get("topic") for e in extract_json_object(resp["content"][0]["text"]).get("assignments", [])}
        except Exception as e:
            print(f"  ✗ batch {bi+1}/{n_batches}: {e}")
            continue
        la = ln = 0
        for i, kw in enumerate(batch, 1):
            tid = by_idx.get(i)
            if tid and tid in valid:
                kw.topic_id = tid; assigned += 1; la += 1
            else:
                nulled += 1; ln += 1
        db.commit()
        print(f"  ✓ batch {bi+1}/{n_batches}: {la} assigned, {ln} → null")

    for t in active:
        t.keyword_count = db.query(ScanKeyword).filter(ScanKeyword.topic_id == t.id).count()
    scan.updated_at = datetime.utcnow()
    db.commit()

    print(f"\n=== SUMMARY align_topics Oral Care / {cfg['brand_name']} ===")
    print(f"  deactivated={deactivated} created={len(targets)} assigned={assigned} null={nulled}")
    for t in active:
        print(f"  {t.name!r} → {t.keyword_count} kw")
    return 0


if __name__ == "__main__":
    sys.exit(main())
