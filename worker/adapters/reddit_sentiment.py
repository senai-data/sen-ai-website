"""Per-thread sentiment classification via Claude Haiku.

v1 contexte-only mode : input is the concatenated LLM citation snippets
(scan_llm_results.citations[].contexte) for a single Reddit URL, NOT
the full thread (Reddit blocks our cloud IP - see reddit_client.py
docstring). At ~200 chars per snippet × ~5 snippets per URL, input is
~1KB - even cheaper than the original full-thread version, ~$0.0003 per
thread, ~$0.03 per 100-thread scan worst case.

Why we run sentiment despite the thin input :
  - At 100 threads per scan, manual triage is impractical.
  - "Competitor mentioned" + "negative sentiment" = highest leverage
    opportunity (user can step in with a better answer).
  - "Competitor mentioned" + "positive sentiment" = lower leverage (the
    crowd already loves them ; harder to flip).
  - "Target brand mentioned + negative" = crisis signal worth flagging.
  - The LLM's chosen snippet usually captures the strongest sentiment
    cue from the thread (it's why the LLM grabbed that exact passage),
    so signal density per byte is high.

Output is bounded to 5 enum values + one short summary :
  sentiment ∈ {positive, negative, neutral, mixed, unclear}
    - unclear : Haiku couldn't read the sentiment because the snippets
      are too thin (no body text from the discussion, just a citation
      marker). This is distinct from "neutral" which means "factual,
      no opinion expressed" - the user reads them very differently.
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

_PROMPT = """You read these snippets that an LLM (ChatGPT or Gemini) captured around a Reddit thread URL when answering a user question. The snippets are short (~200 chars each) and may include the link inline.

You will return sentiment SEPARATELY for the target brand vs the competitor brands so we can detect head-to-head wins and losses. Stay neutral - you are an analyst, not an advocate.

Subreddit: r/{subreddit}
Reddit URL: {url}
Target brand (the user's own brand): {target_brand}
Competitor brands (their rivals): {competitor_brands}

LLM citation snippets:
{snippets}

Return ONLY this JSON (no markdown):

{{
  "target_sentiment":     "positive" | "negative" | "neutral" | "mixed" | "unclear" | "not_mentioned",
  "competitor_sentiment": "positive" | "negative" | "neutral" | "mixed" | "unclear" | "not_mentioned",
  "overall_sentiment":    "positive" | "negative" | "neutral" | "mixed" | "unclear",
  "summary": "one neutral sentence (<= 200 chars) describing what the snippets suggest about the brands in this Reddit discussion. Mention which brand wins or loses if applicable."
}}

Rules for each sentiment value :
- "positive"      : redditors recommend / praise that specific brand
- "negative"      : redditors complain / warn against that specific brand
- "mixed"         : substantial pros AND cons for that specific brand
- "neutral"       : the brand is referenced as fact, no opinion either way
- "unclear"       : the brand is mentioned but the snippet has no body content to read sentiment from (often just "[Source: reddit.com]"). DO NOT default to "neutral" - the user reads these very differently.
- "not_mentioned" : that brand is not present in the snippets at all (use this when the brand list contains brands not actually discussed)

Examples :
- "Bioderma is great for sensitive skin, way better than Ducray" with target=Ducray, competitor=Bioderma :
  → target_sentiment=negative, competitor_sentiment=positive, overall=mixed, summary="Bioderma is preferred over Ducray for sensitive skin."
- "[Source: reddit.com]" with target=Ducray :
  → target_sentiment=unclear, competitor_sentiment=not_mentioned, overall=unclear, summary="Thin snippet cited as source ; no actual discussion content available."
- "Pellicules : voici les meilleurs shampoings selon les utilisateurs" :
  → target_sentiment=not_mentioned, competitor_sentiment=not_mentioned, overall=neutral, summary="Informational thread about dandruff shampoo recommendations ; no specific brand judgment in the snippet."
"""


def _format_snippets(snippets: list[str]) -> str:
    """Join the LLM citation snippets one per bullet. Each is already
    pre-truncated (~200 chars) so we don't need additional trimming."""
    cleaned = [s.strip() for s in (snippets or []) if s and s.strip()]
    if not cleaned:
        return "(no snippets captured)"
    return "\n".join(f"- {s}" for s in cleaned[:10])


def _build_prompt_from_snippets(
    url: str,
    subreddit: str | None,
    snippets: list[str],
    target_brand: str,
    competitor_brands: list[str],
) -> str:
    return _PROMPT.format(
        url=url or "(unknown)",
        subreddit=subreddit or "?",
        snippets=_format_snippets(snippets),
        target_brand=target_brand or "(none specified)",
        competitor_brands=", ".join(competitor_brands) or "(none specified)",
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


_VALID_PER_BRAND = ("positive", "negative", "neutral", "mixed", "unclear", "not_mentioned")
_VALID_OVERALL = ("positive", "negative", "neutral", "mixed", "unclear")


def _norm_per_brand(v) -> str | None:
    """Normalize a per-brand sentiment string. Maps `not_mentioned` to
    None so the DB column stays NULL when the brand isn't in the
    discussion - cleaner SQL and avoids polluting filters."""
    s = (v or "").lower().strip()
    if s == "not_mentioned" or s == "":
        return None
    if s not in _VALID_PER_BRAND:
        return "unclear"
    return s


def _norm_overall(v) -> str:
    s = (v or "").lower().strip()
    if s not in _VALID_OVERALL:
        return "unclear"
    return s


def classify_snippets(
    url: str,
    subreddit: str | None,
    snippets: list[str],
    target_brand: str,
    competitor_brands: list[str],
    api_key: str,
) -> Optional[dict]:
    """Run Haiku on one URL's LLM citation snippets, returning per-brand +
    overall sentiment :
        {
          target_sentiment:     str | None,
          competitor_sentiment: str | None,
          sentiment:            str (overall, never None),
          summary:              str,
        }
    None on failure (no API key, exception). The caller persists the
    row regardless ; per-brand fields default to None when the brand
    isn't in scope on this thread.
    """
    if not api_key:
        return None
    cleaned = [s for s in (snippets or []) if s and s.strip()]
    if not cleaned:
        return None
    prompt = _build_prompt_from_snippets(url, subreddit, cleaned, target_brand, competitor_brands)
    try:
        result = asyncio.run(_call_haiku(prompt, api_key))
    except Exception:  # noqa: BLE001
        logger.exception(f"reddit_sentiment failed for {url}")
        return None
    return {
        "target_sentiment":     _norm_per_brand(result.get("target_sentiment")),
        "competitor_sentiment": _norm_per_brand(result.get("competitor_sentiment")),
        "sentiment":            _norm_overall(result.get("overall_sentiment") or result.get("sentiment")),
        "summary":              (result.get("summary") or "").strip()[:300],
    }
