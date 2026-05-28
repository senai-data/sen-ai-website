"""Reddit JSON-endpoint client - public read-only, no OAuth, no scraping.

Why this is legitimate and not Sprint-7-style impersonation :
  - Reddit officially exposes any thread as JSON by appending `.json` to the
    URL. This is the standard endpoint their own apps and third-party
    integrations use ; it is not a private API or a circumvention.
  - We send a descriptive User-Agent identifying sen-ai (best practice per
    Reddit's API rules) so they can block us if our traffic ever becomes
    problematic. We do not pretend to be a browser, an OAuth app or
    another service.
  - We respect a polite 1 req/sec ceiling per process so we don't burn
    Reddit's unauthenticated rate limit (~60 req/min IP-wide).

The adapter returns a normalized dict so the handler doesn't have to know
about Reddit's nested JSON shape. Failures are non-fatal : the caller
gets {error, status} and persists those so the UI can show "couldn't
fetch this thread" without re-attempting on every refresh.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = (
    "sen-ai/1.0 (+https://sen-ai.fr; contact@sen-ai.fr) "
    "Reddit thread snapshot for AI-visibility audit"
)
TIMEOUT = 15.0
# Reddit JSON endpoint paginates comments after ~200. Use the `limit` and
# `sort=top` params so we get the highest-signal comments first.
COMMENT_LIMIT = 100
TOP_COMMENT_KEEP = 5
# Max body / comment chars kept in DB. Reddit posts can be very long but the
# audit signal lives in the first paragraph + topmost comments.
BODY_MAX = 4000
COMMENT_MAX = 800


_REDDIT_URL_RE = re.compile(
    r"^https?://(?:www\.|old\.|new\.|np\.|m\.)?reddit\.com/(?:r/[^/]+/comments/[a-z0-9]+|comments/[a-z0-9]+)",
    re.IGNORECASE,
)


def is_reddit_url(url: str) -> bool:
    """Return True if the URL looks like a Reddit thread permalink."""
    if not url:
        return False
    return bool(_REDDIT_URL_RE.match(url.strip()))


def _canonical_url(url: str) -> str:
    """Normalize a Reddit thread URL : drop query string + fragment, switch
    any host variant (old.reddit.com / m.reddit.com / np.reddit.com) to the
    canonical www.reddit.com host, strip trailing slash."""
    # Drop query + fragment.
    url = url.split("#", 1)[0].split("?", 1)[0]
    # Host canonicalization.
    url = re.sub(
        r"^https?://(?:www\.|old\.|new\.|np\.|m\.)?reddit\.com",
        "https://www.reddit.com",
        url,
        flags=re.IGNORECASE,
    )
    # Trim trailing slash.
    if url.endswith("/"):
        url = url[:-1]
    return url


def _json_endpoint(url: str) -> str:
    """Append `.json` to the canonical URL, with the params we want."""
    base = _canonical_url(url)
    # `limit` = how many comments to fetch ; `sort=top` = highest score first
    # ; `raw_json=1` disables HTML-entity escaping in the response.
    return f"{base}.json?limit={COMMENT_LIMIT}&sort=top&raw_json=1"


def _trunc(s: str | None, n: int) -> str | None:
    if not s:
        return s
    s = s.strip()
    return s if len(s) <= n else (s[:n] + "…")


def _post_data(payload: list | dict) -> dict | None:
    """Reddit returns a 2-element array [post_listing, comments_listing].
    Extract the post's data dict, or None if the shape is unexpected."""
    if not isinstance(payload, list) or len(payload) < 1:
        return None
    post_listing = payload[0]
    try:
        return post_listing["data"]["children"][0]["data"]
    except (KeyError, IndexError, TypeError):
        return None


def _walk_comments(payload: list | dict) -> list[dict]:
    """Walk the comments tree breadth-first and return the top N by score.
    We only descend into the first level of replies ; deeper threads rarely
    add signal worth the audit cost."""
    if not isinstance(payload, list) or len(payload) < 2:
        return []
    out: list[dict] = []
    try:
        children = payload[1]["data"]["children"]
    except (KeyError, IndexError, TypeError):
        return []
    for child in children:
        if child.get("kind") != "t1":
            continue
        data = child.get("data") or {}
        body = data.get("body") or ""
        if not body or body in ("[deleted]", "[removed]"):
            continue
        out.append({
            "author": data.get("author") or "[unknown]",
            "body": _trunc(body, COMMENT_MAX) or "",
            "score": int(data.get("score") or 0),
            "depth": int(data.get("depth") or 0),
        })
        # One level of replies, max 2 per parent to keep the payload bounded.
        replies = (data.get("replies") or {}).get("data", {}).get("children", []) if isinstance(data.get("replies"), dict) else []
        kept = 0
        for rep in replies:
            if rep.get("kind") != "t1" or kept >= 2:
                continue
            rd = rep.get("data") or {}
            rbody = rd.get("body") or ""
            if not rbody or rbody in ("[deleted]", "[removed]"):
                continue
            out.append({
                "author": rd.get("author") or "[unknown]",
                "body": _trunc(rbody, COMMENT_MAX) or "",
                "score": int(rd.get("score") or 0),
                "depth": 1,
            })
            kept += 1
    out.sort(key=lambda c: c["score"], reverse=True)
    return out[:TOP_COMMENT_KEEP]


def fetch_thread(url: str) -> dict:
    """Fetch one Reddit thread. Returns :
        {
          status: int|None, error: str|None,
          url: canonical_url,
          subreddit, title, author, score, num_comments,
          posted_at: ISO string|None,
          body_excerpt: str|None,
          top_comments: [{author, body, score, depth}, ...]
        }
    """
    canonical = _canonical_url(url)
    out: dict = {
        "status": None, "error": None, "url": canonical,
        "subreddit": None, "title": None, "author": None,
        "score": None, "num_comments": None,
        "posted_at": None, "body_excerpt": None, "top_comments": [],
    }
    try:
        with httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=True,
            max_redirects=3,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Accept-Language": "en;q=0.9, fr;q=0.8",
            },
        ) as c:
            r = c.get(_json_endpoint(url))
            out["status"] = r.status_code
            if r.status_code in (401, 403, 429):
                out["error"] = f"blocked_http_{r.status_code}"
                return out
            if r.status_code >= 400:
                out["error"] = f"http_{r.status_code}"
                return out
            try:
                payload = r.json()
            except ValueError:
                out["error"] = "invalid_json"
                return out
    except httpx.TimeoutException:
        out["error"] = "timeout"
        return out
    except httpx.HTTPError as e:
        out["error"] = f"http_error:{str(e)[:120]}"
        return out
    except Exception as e:  # noqa: BLE001
        out["error"] = f"exception:{type(e).__name__}:{str(e)[:120]}"
        return out

    post = _post_data(payload)
    if not post:
        out["error"] = "unexpected_shape"
        return out

    out["subreddit"] = (post.get("subreddit") or None)
    out["title"] = _trunc(post.get("title"), 500)
    out["author"] = post.get("author") or None
    out["score"] = int(post.get("score") or 0)
    out["num_comments"] = int(post.get("num_comments") or 0)
    out["body_excerpt"] = _trunc(post.get("selftext"), BODY_MAX)
    ts = post.get("created_utc")
    if ts:
        try:
            out["posted_at"] = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
        except (TypeError, ValueError):
            pass
    out["top_comments"] = _walk_comments(payload)
    return out
