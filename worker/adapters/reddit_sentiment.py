"""Per-thread sentiment classification via Claude Haiku.

Light-weight LLM call : single prompt, structured JSON out, capped at
~4-6KB of input per thread (title + body excerpt + top comments). At
Haiku 4.5 prices (~$0.80/M input, $4/M output) this costs ~$0.001-0.003
per thread, so the default cap of 100 threads per scan lands well under
the $1/day/client LLM budget guard.

Why we run sentiment here instead of letting the user read the snippet :
  - At 100 threads per scan, manual triage is impractical.
  - "Competitor mentioned" + "negative sentiment" = highest leverage
    opportunity (user can step in with a better answer).
  - "Competitor mentioned" + "positive sentiment" = lower leverage (the
    crowd already loves them ; harder to flip).
  - "Target brand mentioned + negative" = crisis signal worth flagging.

Output is bounded to 4 enum values + one short summary :
  sentiment ∈ {positive, negative, neutral, mixed}
  summary  ≤ 200 chars, neutral observer voice
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

HAIKU_MODEL = "claude-haiku-4-5-20251001"
TIMEOUT = 60.0

_PROMPT = """You read this Reddit thread and tell me the overall sentiment toward the brands or products being discussed. Stay neutral - you are an analyst, not an advocate.

Thread title: {title}

Subreddit: r/{subreddit}

Original post:
{body}

Top comments:
{comments}

Mentioned brands of interest: {brands}

Return ONLY this JSON (no markdown):

{{
  "sentiment": "positive" | "negative" | "neutral" | "mixed",
  "summary": "one neutral sentence (<= 200 chars) describing what the thread says about the brand(s) of interest. If no brand is mentioned, describe the user's intent. Do not paraphrase upvotes or moderator notes."
}}

Rules:
- "positive" : redditors recommend / praise the brand(s)
- "negative" : redditors complain / warn against the brand(s)
- "mixed"    : substantial pros AND cons in the discussion
- "neutral"  : the brand is referenced as fact, no clear sentiment ; or the thread is informational with no brand opinion
"""


def _format_comments(comments: list[dict]) -> str:
    if not comments:
        return "(no comments)"
    out = []
    for c in comments[:5]:
        score = c.get("score") or 0
        author = c.get("author") or "?"
        body = (c.get("body") or "").strip()
        if not body:
            continue
        # Truncate long comments inline so the prompt stays bounded.
        if len(body) > 500:
            body = body[:500] + "…"
        out.append(f"[{score}↑] {author}: {body}")
    return "\n".join(out) if out else "(no comments)"


def _build_prompt(thread: dict, brand_names: list[str]) -> str:
    body = (thread.get("body_excerpt") or "").strip()
    if len(body) > 2000:
        body = body[:2000] + "…"
    return _PROMPT.format(
        title=thread.get("title") or "(no title)",
        subreddit=thread.get("subreddit") or "?",
        body=body or "(no body)",
        comments=_format_comments(thread.get("top_comments") or []),
        brands=", ".join(brand_names) or "(none specified)",
    )


async def _call_haiku(prompt: str, api_key: str) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": HAIKU_MODEL,
                "max_tokens": 300,
                "temperature": 0.0,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        body = data["content"][0]["text"]
        # Be permissive : the model sometimes wraps in ```json...```.
        body = body.strip()
        if body.startswith("```"):
            body = body.strip("` \n")
            if body.lower().startswith("json"):
                body = body[4:].lstrip()
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            # Salvage : find the first {...} block.
            i, j = body.find("{"), body.rfind("}")
            if i >= 0 and j > i:
                try:
                    return json.loads(body[i:j + 1])
                except json.JSONDecodeError:
                    pass
            raise


def classify_thread(thread: dict, brand_names: list[str], api_key: str) -> Optional[dict]:
    """Run Haiku on one thread. Returns {sentiment, summary} or None on
    failure. Always non-fatal - the caller persists the row regardless."""
    if not api_key:
        return None
    prompt = _build_prompt(thread, brand_names)
    try:
        result = asyncio.run(_call_haiku(prompt, api_key))
    except Exception:  # noqa: BLE001
        logger.exception(f"reddit_sentiment failed for {thread.get('url')}")
        return None
    sentiment = (result.get("sentiment") or "").lower().strip()
    if sentiment not in ("positive", "negative", "neutral", "mixed"):
        sentiment = "neutral"
    summary = (result.get("summary") or "").strip()[:300]
    return {"sentiment": sentiment, "summary": summary}
