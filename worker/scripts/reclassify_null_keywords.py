"""Follow-up: classify scan_keywords with topic_id=NULL into the 4 active topics.

Run after `align_topics_aderma.py` if its Claude classification step crashed
midway. Picks up where it left off without re-touching the 4 already-correct
topics. Idempotent — running twice = no-op (orphans drop to 0).

Args via env :
    SCAN_ID=<uuid>
    TARGET_TOPIC_NAMES=  comma-separated names of the 4 active topics
                         (else auto-discovered from `scan_topics` where
                         is_active=True)
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime

import httpx

sys.path.insert(0, '/app')

from models import SessionLocal, Scan, ScanTopic, ScanKeyword
from config import settings
from adapters.json_utils import extract_json_object

SCAN_ID = os.environ.get("SCAN_ID", "")
_BATCH = 60
_HAIKU = "claude-haiku-4-5-20251001"


def _build_prompt(topics: list[ScanTopic], keywords: list[str]) -> str:
    topics_block = "\n".join(
        f'- "{t.id}" — {t.name}: {t.description or "(no description)"}'
        for t in topics
    )
    kws_block = "\n".join(f'{i+1}. {k}' for i, k in enumerate(keywords))
    return f"""Assign each keyword to ONE topic by its UUID, or "null" if it doesn't fit any.

# Active topics
{topics_block}
- "null" — keyword is out of scope for this brand (generic, off-vertical, ambiguous).

# Keywords
{kws_block}

# Output (JSON only, same order)
{{
  "assignments": [
    {{"i": 1, "topic": "<topic-uuid or 'null'>"}}
  ]
}}

Conservative: when in doubt → "null"."""


async def _call(api_key: str, prompt: str) -> dict:
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
        return resp.json()


def main():
    if not SCAN_ID:
        print("ERROR: SCAN_ID env var required")
        return 1
    if not settings.anthropic_api_key:
        print("ERROR: ANTHROPIC_API_KEY missing")
        return 1

    db = SessionLocal()
    scan = db.query(Scan).filter(Scan.id == SCAN_ID).first()
    if not scan:
        print(f"ERROR: scan {SCAN_ID} not found")
        return 1
    print(f"Scan: {scan.id} ({scan.domain})")

    active_topics = (
        db.query(ScanTopic)
        .filter(ScanTopic.scan_id == SCAN_ID, ScanTopic.is_active == True)
        .order_by(ScanTopic.display_order)
        .all()
    )
    print(f"Active topics ({len(active_topics)}):")
    for t in active_topics:
        print(f"  {str(t.id)[:8]}… {t.name!r}")

    orphans = (
        db.query(ScanKeyword)
        .filter(ScanKeyword.scan_id == SCAN_ID, ScanKeyword.topic_id.is_(None))
        .all()
    )
    print(f"\nOrphan keywords (topic_id IS NULL): {len(orphans)}")
    if not orphans:
        print("Nothing to do.")
        return 0

    valid_topic_ids = {str(t.id) for t in active_topics}
    assigned = 0
    nulled = 0

    n_batches = (len(orphans) + _BATCH - 1) // _BATCH
    for bi in range(n_batches):
        batch = orphans[bi * _BATCH:(bi + 1) * _BATCH]
        prompt = _build_prompt(active_topics, [k.keyword for k in batch])
        try:
            resp = asyncio.run(_call(settings.anthropic_api_key, prompt))
            parsed = extract_json_object(resp["content"][0]["text"])
            by_idx = {entry.get("i"): entry.get("topic") for entry in parsed.get("assignments", [])}
        except Exception as e:
            print(f"  ✗ batch {bi+1}/{n_batches} failed: {e}")
            continue

        local_assigned = 0
        local_nulled = 0
        for i, kw in enumerate(batch, 1):
            tid = by_idx.get(i)
            if tid and tid in valid_topic_ids:
                kw.topic_id = tid
                assigned += 1
                local_assigned += 1
            else:
                # keep NULL
                nulled += 1
                local_nulled += 1
        db.commit()
        print(f"  ✓ batch {bi+1}/{n_batches}: {local_assigned} assigned, {local_nulled} → null")

    # Recompute keyword_count
    for t in active_topics:
        t.keyword_count = db.query(ScanKeyword).filter(ScanKeyword.topic_id == t.id).count()
    scan.updated_at = datetime.utcnow()
    db.commit()

    print(f"\n=========================")
    print(f"SUMMARY")
    print(f"  orphans processed: {len(orphans)}")
    print(f"  assigned         : {assigned}")
    print(f"  kept null        : {nulled}")
    print(f"\nActive topics now:")
    for t in active_topics:
        print(f"  {t.name!r} → {t.keyword_count} keywords")
    print(f"=========================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
