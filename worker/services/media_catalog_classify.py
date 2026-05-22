"""Phase MR.4 #2 — Haiku classifier for media_catalog audience + voice.

Infers, per media domain, a small set of audience tags + a one-line editorial
voice descriptor from the domain name and the topics it's been cited on. These
populate `media_catalog.audience_tags[]` + `media_catalog.editorial_voice`,
which activate the previously-stubbed `persona_audience` (2.0) and
`editorial_voice_match` (1.5) scoring weights in media_replacement.

Multi-lingual / multi-vertical : the only inputs are the domain + its
topic_areas (already vertical-agnostic). No hardcoded categories.

Cost : Haiku, ~$0.0005/domain, batched. Wired into the nightly
discover_media_catalog cron (classify rows where audience_tags is empty),
capped per run so it doesn't balloon.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx

from adapters.json_utils import extract_json_object
from config import settings
from utils import max_tokens_for

logger = logging.getLogger(__name__)

# Batch size — keep prompt bounded + Haiku accurate (drops items past ~40).
CLASSIFY_BATCH_SIZE = 30

# How many catalog rows to classify per cron run. Bounds Haiku spend ; the
# cron runs nightly so the catalog fills over a few days like Babbar.
CLASSIFY_MAX_PER_RUN = 300

_PROMPT = """You analyze MEDIA WEBSITES. For each one, infer its typical AUDIENCE and EDITORIAL VOICE \
from the domain name and the topics it publishes on.

For each media, return:
- "audience_tags" : 3 to 6 SHORT lowercase tags describing who reads it \
(e.g. "parents", "young women", "health-conscious adults", "diy enthusiasts", \
"b2b decision-makers", "seniors"). Generic, reusable tags — NOT full sentences.
- "editorial_voice" : ONE short phrase describing the tone / register \
(e.g. "accessible health journalism", "expert clinical", "lifestyle and beauty", \
"practical how-to", "investigative news"). Max 8 words.

Be realistic — infer from what the outlet actually is. If unsure, give your best \
general guess from the domain. Never leave fields empty.

Media to analyze (domain => topics it covers):
{batch}

Return ONLY valid JSON, no markdown:

{{
  "media": [
    {{"domain": "...", "audience_tags": ["...", "..."], "editorial_voice": "..."}},
    ...
  ]
}}
"""


async def _call_haiku(prompt: str, api_key: str, model: str) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens_for(model, cap=4096),
                "temperature": 0.0,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"]
        parsed = extract_json_object(text)
        parsed["_usage"] = data.get("usage", {})
        return parsed


def _classify_batch(
    batch: list[tuple[str, list[str]]],
    api_key: str,
    model: str,
) -> dict[str, dict]:
    """Classify one batch of (domain, topic_areas). Returns {domain: {audience_tags, editorial_voice}}."""
    if not batch:
        return {}
    batch_str = "\n".join(
        f'"{domain}" => {json.dumps(", ".join(topics[:6]) or "(unknown topics)", ensure_ascii=False)}'
        for domain, topics in batch
    )
    prompt = _PROMPT.format(batch=batch_str)
    try:
        result = asyncio.run(_call_haiku(prompt, api_key, model))
    except Exception:
        logger.exception("media_catalog_classify: Haiku call failed")
        return {}
    result.pop("_usage", None)

    out: dict[str, dict] = {}
    for entry in (result.get("media") or []):
        if not isinstance(entry, dict):
            continue
        domain = (entry.get("domain") or "").strip().lower()
        if not domain:
            continue
        tags = entry.get("audience_tags") or []
        tags = [str(t).strip().lower() for t in tags if str(t).strip()][:6]
        voice = (entry.get("editorial_voice") or "").strip()[:120]
        out[domain] = {"audience_tags": tags, "editorial_voice": voice}
    return out


def classify_catalog_rows(db, *, max_rows: int = CLASSIFY_MAX_PER_RUN) -> dict:
    """Classify catalog rows whose audience_tags is empty. Returns stats dict.

    Picks the highest-signal rows first (llm_citation_decayed DESC) so the media
    that actually surface in suggestions get tagged soonest. Per-batch commit so
    a mid-run failure keeps prior batches.
    """
    from sqlalchemy import text

    api_key = settings.anthropic_api_key
    if not api_key:
        logger.warning("media_catalog_classify: no anthropic_api_key — skipping")
        return {"classified": 0, "error": "no_api_key"}

    model = "claude-haiku-4-5-20251001"

    rows = db.execute(text("""
        SELECT domain, country, language, topic_areas
          FROM media_catalog
         WHERE (audience_tags IS NULL OR cardinality(audience_tags) = 0)
         ORDER BY llm_citation_decayed DESC
         LIMIT :lim
    """), {"lim": max_rows}).fetchall()
    if not rows:
        return {"classified": 0, "remaining": 0}

    # Dedup by domain for the LLM call (same domain may exist for >1 locale) ;
    # apply the result back to all matching (domain, country, language) rows.
    by_domain_topics: dict[str, list[str]] = {}
    for r in rows:
        by_domain_topics.setdefault(r.domain, list(r.topic_areas or []))

    domains = list(by_domain_topics.items())
    classified = 0
    for i in range(0, len(domains), CLASSIFY_BATCH_SIZE):
        chunk = domains[i:i + CLASSIFY_BATCH_SIZE]
        results = _classify_batch(chunk, api_key, model)
        for domain, info in results.items():
            tags = info.get("audience_tags") or []
            voice = info.get("editorial_voice") or ""
            if not tags and not voice:
                continue
            try:
                db.execute(text("""
                    UPDATE media_catalog
                       SET audience_tags = CAST(:tags AS text[]),
                           editorial_voice = :voice,
                           updated_at = NOW()
                     WHERE domain = :d
                """), {"tags": tags, "voice": voice, "d": domain})
                db.commit()
                classified += 1
            except Exception:
                db.rollback()
                logger.exception(f"media_catalog_classify: update failed for {domain}")

    logger.info(f"media_catalog_classify: classified {classified}/{len(domains)} domains")
    return {"classified": classified, "domains_seen": len(domains)}
