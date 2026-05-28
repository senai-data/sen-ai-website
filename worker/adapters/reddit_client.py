"""Reddit OAuth-authenticated client - legitimate Reddit API consumer.

Why OAuth and not the bare *.json endpoint :
  - Reddit blocks ALL cloud-provider IPs from the unauthenticated *.json
    endpoint (confirmed 2026-05-28 : Hetzner IP returns HTTP 403 even with
    a Mozilla User-Agent). The block is IP-based, not UA-based.
  - OAuth-authenticated apps get explicit allowance + 100 QPM rate ; this
    is the path Reddit publishes in their API rules.
  - We use the app-only auth flow (grant_type=client_credentials) so no
    Reddit user account is involved - the worker authenticates as the
    registered sen-ai app.

Setup (one-time, manual) :
  1. Register a "script" type app at https://www.reddit.com/prefs/apps
  2. Copy client_id (14 chars) and secret (~27 chars)
  3. Set REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET env vars on the worker

The adapter caches the OAuth token in memory until it expires (typically
24h). Token fetch failures fall back to the unauthenticated endpoint with
a warning (will likely 403 on cloud IPs but works on dev laptops).

The adapter returns a normalized dict so the handler doesn't have to know
about Reddit's nested JSON shape. Failures are non-fatal : the caller
gets {error, status} and persists those so the UI can show "couldn't
fetch this thread" without re-attempting on every refresh.
"""
from __future__ import annotations

import base64
import logging
import re
import threading
import time
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = (
    "sen-ai/1.0 (+https://sen-ai.fr; contact@sen-ai.fr) "
    "Reddit thread snapshot for AI-visibility audit"
)
TIMEOUT = 15.0


# ── OAuth token cache ────────────────────────────────────────────────────
#
# Reddit app-only tokens last ~24h. We cache a single token per worker
# process under a lock so concurrent fetches (if we ever go multi-threaded)
# don't trigger a thundering herd on the token endpoint. The expires_at
# margin (60 s before real expiry) protects against in-flight requests
# straddling the boundary.

_token_lock = threading.Lock()
_token_cache: dict = {"access_token": None, "expires_at": 0.0}


def _get_oauth_token() -> str | None:
    """Return a valid Reddit OAuth bearer token, refreshing if needed.

    Returns None if REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET are missing or
    the token endpoint fails. Caller falls back to www.reddit.com.
    """
    from config import settings

    client_id = (settings.reddit_client_id or "").strip()
    client_secret = (settings.reddit_client_secret or "").strip()
    if not client_id or not client_secret:
        return None

    now = time.time()
    with _token_lock:
        cached = _token_cache.get("access_token")
        expires_at = float(_token_cache.get("expires_at") or 0.0)
        if cached and now < (expires_at - 60):
            return cached

        # Refresh.
        try:
            basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
            with httpx.Client(timeout=TIMEOUT) as c:
                r = c.post(
                    "https://www.reddit.com/api/v1/access_token",
                    headers={
                        "Authorization": f"Basic {basic}",
                        "User-Agent": USER_AGENT,
                    },
                    data={"grant_type": "client_credentials"},
                )
            if r.status_code != 200:
                logger.warning(
                    f"reddit_oauth token fetch failed : HTTP {r.status_code} {r.text[:200]}"
                )
                return None
            data = r.json()
        except Exception:  # noqa: BLE001
            logger.exception("reddit_oauth token fetch raised")
            return None

        token = data.get("access_token")
        if not token:
            logger.warning(f"reddit_oauth response missing access_token : {data}")
            return None
        expires_in = int(data.get("expires_in") or 3600)
        _token_cache["access_token"] = token
        _token_cache["expires_at"] = now + expires_in
        logger.info(f"reddit_oauth token refreshed, expires in {expires_in}s")
        return token
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


def _json_endpoint(url: str, authed: bool = False) -> str:
    """Append `.json` to the canonical URL, with the params we want. When
    OAuth-authenticated, swap the host to oauth.reddit.com (the bearer
    token only works against that host)."""
    base = _canonical_url(url)
    if authed:
        base = base.replace("https://www.reddit.com", "https://oauth.reddit.com", 1)
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

    # Try OAuth first (bypasses Reddit's cloud-IP block on www.reddit.com).
    # Fall back to www.reddit.com only if no credentials are configured ;
    # we don't fall back on a 401 because that means our credentials are
    # wrong, not that we should hammer the unauthenticated endpoint.
    token = _get_oauth_token()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Accept-Language": "en;q=0.9, fr;q=0.8",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    endpoint = _json_endpoint(url, authed=bool(token))

    try:
        with httpx.Client(
            timeout=TIMEOUT,
            follow_redirects=True,
            max_redirects=3,
            headers=headers,
        ) as c:
            r = c.get(endpoint)
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
