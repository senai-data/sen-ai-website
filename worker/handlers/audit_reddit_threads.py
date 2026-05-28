"""Handler : Sprint 8 Reddit opportunity finder.

For each scan we surface Reddit threads the LLMs already cite. Each
thread is fetched via Reddit's public JSON endpoint (no scraping, no
OAuth), then classified :

  1. Is the target brand mentioned in (title + body + top comments) ?
  2. Which competitor brands are mentioned ?
  3. Sentiment via Haiku (capped, cheap, optional via job payload).
  4. Composite leverage_score (0-100) drives the UI sort order so the
     user sees the highest-opportunity threads first.

Source of URLs : ONLY Reddit citations the LLMs already produced for
this scan. No external SERP discovery in v1. Same architectural choice
as Sprint 7 - mine what wins right now, broaden later.

Caps :
  - 100 threads max per scan, polite 0.6s delay between fetches.
  - ~$0.10 max LLM cost per scan (100 × ~$0.001 Haiku per thread).
  - Sentiment can be disabled via job payload `sentiment=false`.

Cost : LLM-bounded by the per-thread cap above. Plain HTTP for the
Reddit fetches.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Iterable
from urllib.parse import urlparse

from sqlalchemy import text as _text
from sqlalchemy.orm import Session

from adapters.reddit_client import fetch_thread, is_reddit_url, _canonical_url
from adapters.reddit_sentiment import classify_thread

logger = logging.getLogger(__name__)

FETCH_DELAY_SECONDS = 0.6   # Reddit unauthenticated rate is ~1 req/s IP-wide.
MAX_THREADS_PER_RUN = 100
MIN_BRAND_LEN_FOR_REGEX = 3  # avoid matching "Vi" inside "Vichy" et al.


def _cited_reddit_urls(db: Session, scan_id: str, limit: int) -> list[dict]:
    """Find every Reddit thread URL cited by an LLM during this scan, with
    citation counts and the questions that prompted them.

    URL is normalized to the canonical Reddit form (strip query/fragment,
    rewrite host variants) at projection time so `/r/x/comments/123` and
    `old.reddit.com/r/x/comments/123/?ref=foo` collapse into one row.
    """
    sql = _text(
        """
        WITH cites AS (
          SELECT slr.id AS slr_id,
                 slr.question_id,
                 slr.provider,
                 citation->>'url' AS raw_url,
                 lower(citation->>'domaine') AS domaine,
                 citation->>'contexte' AS contexte
            FROM scan_llm_results slr
            JOIN LATERAL jsonb_array_elements(slr.citations) AS citation ON true
           WHERE slr.scan_id = :scan_id
             AND citation->>'url' IS NOT NULL
             AND (lower(citation->>'domaine') LIKE '%reddit.com'
                  OR citation->>'url' ILIKE '%reddit.com/%')
        )
        SELECT c.raw_url AS url,
               COUNT(*) AS cites,
               jsonb_agg(DISTINCT jsonb_build_object(
                 'question_id', c.question_id::text,
                 'question',    sq.question,
                 'provider',    c.provider,
                 'contexte',    c.contexte,
                 'slr_id',      c.slr_id::text
               )) FILTER (WHERE sq.question IS NOT NULL) AS questions
          FROM cites c
          LEFT JOIN scan_questions sq ON sq.id = c.question_id
         GROUP BY c.raw_url
         ORDER BY cites DESC
        """
    )
    raw_rows = db.execute(sql, {"scan_id": scan_id}).fetchall()

    # Re-aggregate on the canonical URL form (the SQL above keeps raw URLs
    # because Postgres split_part chains are awkward to filter on ; we'd
    # rather do it in Python).
    bucket: dict[str, dict] = {}
    for r in raw_rows:
        url = r[0]
        if not is_reddit_url(url):
            continue
        canonical = _canonical_url(url)
        b = bucket.get(canonical)
        if b is None:
            b = {"url": canonical, "citation_count": 0, "winning_questions": []}
            bucket[canonical] = b
        b["citation_count"] += int(r[1] or 0)
        seen = {(q.get("question_id"), q.get("provider")) for q in b["winning_questions"]}
        for q in (r[2] or []):
            key = (q.get("question_id"), q.get("provider"))
            if key in seen:
                continue
            seen.add(key)
            b["winning_questions"].append(q)

    out = sorted(bucket.values(), key=lambda x: -x["citation_count"])
    return out[:limit]


def _scan_brands(db: Session, scan_id: str) -> tuple[set[str], set[str]]:
    """Return (target_names, competitor_names) - lower-cased canonical names
    + aliases of every brand classified for this scan. Used by the regex
    mention matcher below.
    """
    target: set[str] = set()
    competitor: set[str] = set()
    rows = db.execute(_text(
        """
        SELECT cb.name, cb.canonical_name, cb.aliases, sbc.classification, sbc.is_focus
          FROM scan_brand_classifications sbc
          JOIN client_brands cb ON cb.id = sbc.brand_id
         WHERE sbc.scan_id = :scan_id
           AND sbc.classification IN ('my_brand', 'competitor')
        """
    ), {"scan_id": scan_id}).fetchall()
    for name, canonical, aliases, cls, _focus in rows:
        names = {n for n in [name, canonical, *(aliases or [])] if n}
        cleaned = {n.lower().strip() for n in names if n and len(n) >= MIN_BRAND_LEN_FOR_REGEX}
        if cls == "my_brand":
            target |= cleaned
        else:
            competitor |= cleaned
    return target, competitor


def _build_corpus(thread: dict) -> str:
    """Concatenate title + body + comments for regex mention matching. The
    text is lowercased once at the end."""
    parts: list[str] = []
    if thread.get("title"):
        parts.append(str(thread["title"]))
    if thread.get("body_excerpt"):
        parts.append(str(thread["body_excerpt"]))
    for c in thread.get("top_comments") or []:
        body = c.get("body")
        if body:
            parts.append(str(body))
    return "\n".join(parts).lower()


def _detect_brands(corpus_lower: str, candidates: set[str]) -> set[str]:
    """Find which brand names appear as whole words in the lowercased
    corpus. Skip very short tokens so we don't match 'fr' inside any
    English text or 'svr' inside 'service' etc."""
    if not corpus_lower or not candidates:
        return set()
    hits: set[str] = set()
    for name in candidates:
        if len(name) < MIN_BRAND_LEN_FOR_REGEX:
            continue
        # Word-boundary match, case-insensitive (corpus already lower).
        pattern = r"\b" + re.escape(name) + r"\b"
        if re.search(pattern, corpus_lower):
            hits.add(name)
    return hits


def _classify(target_hits: set[str], competitor_hits: set[str]) -> str:
    if competitor_hits and not target_hits:
        return "competitor_wins"
    if target_hits:
        return "you_win"
    return "neutral"


def _leverage_score(thread: dict, classification: str, sentiment: str | None) -> int:
    """Composite 0-100 priority score : engagement + classification +
    sentiment lever. See migration 050 comment for the formula breakdown."""
    import math

    score = int(thread.get("score") or 0)
    comments = int(thread.get("num_comments") or 0)

    # Engagement : log-scaled so a 1k-upvote thread doesn't crush a 100-upvote
    # one. Normalized assuming log10(score)*log10(comments+1) tops around 8.
    engagement_raw = math.log10(max(score, 1) + 1) * math.log10(comments + 1)
    engagement = min(55, int(round(engagement_raw / 8.0 * 55)))

    cls_pts = {"competitor_wins": 25, "neutral": 10, "you_win": 0}.get(classification, 0)

    sent_pts = 0
    if sentiment == "negative":
        # Negative-about-the-competitor (if a competitor is in the convo)
        # = high leverage. We don't know who the negativity targets without
        # a finer pass ; treat all negative threads as opportunity for v1.
        sent_pts = 20
    elif sentiment in ("neutral", "mixed", None):
        sent_pts = 10
    elif sentiment == "positive":
        sent_pts = 0

    return max(0, min(100, engagement + cls_pts + sent_pts))


def execute(job_payload: dict, scan_id: str, db: Session) -> dict:
    """Audit Reddit threads cited by LLMs in this scan.

    job_payload :
      - reset (bool)         : drop existing rows before re-running
      - limit (int)          : cap thread count (default MAX_THREADS_PER_RUN)
      - sentiment (bool)     : run Haiku sentiment pass (default true)
    """
    from models import Scan, ScanRedditThread
    from config import settings

    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan:
        raise RuntimeError("Scan not found")

    reset = bool(job_payload.get("reset"))
    limit = int(job_payload.get("limit") or MAX_THREADS_PER_RUN)
    run_sentiment = bool(job_payload.get("sentiment", True))

    if reset:
        db.query(ScanRedditThread).filter(ScanRedditThread.scan_id == scan_id).delete()
        db.commit()

    pairs = _cited_reddit_urls(db, scan_id, limit)
    if not pairs:
        logger.info(f"audit_reddit_threads: no Reddit citations in scan {scan_id}")
        return {"threads": 0, "errors": 0, "skipped": 0}

    target_names, competitor_names = _scan_brands(db, scan_id)
    all_brand_names = sorted(list(target_names | competitor_names))[:30]
    api_key = (settings.anthropic_api_key or "").strip() if run_sentiment else ""

    fetched_ok = 0
    fetch_errors = 0
    sentiment_runs = 0

    for p in pairs:
        url = p["url"]
        fetched = fetch_thread(url)
        fetch_status = fetched["status"]
        fetch_err = fetched["error"]

        thread_payload = {
            "url": url,
            "subreddit": fetched.get("subreddit"),
            "title": fetched.get("title"),
            "author": fetched.get("author"),
            "score": fetched.get("score"),
            "num_comments": fetched.get("num_comments"),
            "posted_at": fetched.get("posted_at"),
            "body_excerpt": fetched.get("body_excerpt"),
            "top_comments": fetched.get("top_comments") or [],
        }

        classification = None
        target_mentioned = False
        competitors_hit: list[str] = []
        sentiment = None
        sentiment_summary = None
        leverage = None

        if not fetch_err and thread_payload["title"]:
            corpus = _build_corpus(thread_payload)
            target_hits = _detect_brands(corpus, target_names)
            competitor_hits = _detect_brands(corpus, competitor_names)
            target_mentioned = bool(target_hits)
            competitors_hit = sorted(list(competitor_hits))
            classification = _classify(target_hits, competitor_hits)

            if api_key and (target_mentioned or competitors_hit):
                # Only spend LLM budget on threads with at least one brand
                # in scope. Pure context noise gets sentiment=None.
                res = classify_thread(thread_payload, all_brand_names, api_key)
                if res:
                    sentiment = res.get("sentiment")
                    sentiment_summary = res.get("summary")
                    sentiment_runs += 1

            leverage = _leverage_score(thread_payload, classification, sentiment)
            fetched_ok += 1
        else:
            fetch_errors += 1

        existing = (
            db.query(ScanRedditThread)
            .filter(ScanRedditThread.scan_id == scan_id, ScanRedditThread.url == url)
            .first()
        )
        if existing:
            existing.subreddit = thread_payload["subreddit"]
            existing.title = thread_payload["title"]
            existing.author = thread_payload["author"]
            existing.score = thread_payload["score"]
            existing.num_comments = thread_payload["num_comments"]
            existing.posted_at = _parse_iso(thread_payload["posted_at"])
            existing.fetch_status = fetch_status
            existing.fetch_error = fetch_err
            existing.citation_count = p["citation_count"]
            existing.target_mentioned = target_mentioned
            existing.competitors_mentioned = competitors_hit
            existing.classification = classification
            existing.sentiment = sentiment
            existing.sentiment_summary = sentiment_summary
            existing.body_excerpt = thread_payload["body_excerpt"]
            existing.top_comments = thread_payload["top_comments"]
            existing.winning_questions = p["winning_questions"]
            existing.leverage_score = leverage
        else:
            db.add(ScanRedditThread(
                scan_id=scan_id,
                url=url,
                subreddit=thread_payload["subreddit"],
                title=thread_payload["title"],
                author=thread_payload["author"],
                score=thread_payload["score"],
                num_comments=thread_payload["num_comments"],
                posted_at=_parse_iso(thread_payload["posted_at"]),
                fetch_status=fetch_status,
                fetch_error=fetch_err,
                citation_count=p["citation_count"],
                target_mentioned=target_mentioned,
                competitors_mentioned=competitors_hit,
                classification=classification,
                sentiment=sentiment,
                sentiment_summary=sentiment_summary,
                body_excerpt=thread_payload["body_excerpt"],
                top_comments=thread_payload["top_comments"],
                winning_questions=p["winning_questions"],
                leverage_score=leverage,
            ))

        if (fetched_ok + fetch_errors) % 10 == 0:
            db.commit()
            logger.info(
                f"reddit audit progress {fetched_ok + fetch_errors}/{len(pairs)} "
                f"(ok={fetched_ok} err={fetch_errors} sentiment_runs={sentiment_runs})"
            )

        time.sleep(FETCH_DELAY_SECONDS)

    db.commit()
    logger.info(
        f"reddit audit complete : threads={fetched_ok} errors={fetch_errors} "
        f"sentiment_runs={sentiment_runs}"
    )
    return {
        "threads": fetched_ok,
        "errors": fetch_errors,
        "sentiment_runs": sentiment_runs,
        "total": len(pairs),
    }


def _parse_iso(s: str | None):
    if not s:
        return None
    try:
        from datetime import datetime
        # fromisoformat tolerates +00:00 ; trailing Z is not parseable < 3.11.
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None
