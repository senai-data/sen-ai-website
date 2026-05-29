"""YouTube oEmbed adapter for Sprint 10 creator mapping.

YouTube exposes a free, no-key oEmbed endpoint :
    GET https://www.youtube.com/oembed?url=<video_url>&format=json

Returns JSON :
    {
      "title": "Video title",
      "author_name": "Creator name",
      "author_url": "https://www.youtube.com/@channel",
      "thumbnail_url": "...",
      "html": "<iframe...></iframe>",
      ...
    }

The endpoint is documented in YouTube's developer docs and is the same
tier as the embed widget. No API key, no quota at our volumes (we cap
runs at 200 videos per scan with a 0.4s polite throttle). Private,
deleted or age-restricted videos return HTTP 401/403/404 - we surface
the status so the UI can chip them as 'unavailable'.

The author_url returned for legacy channels (no /@handle yet) uses
/channel/UC... format. We store it verbatim ; it's the same canonical
the YouTube web UI uses.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

logger = logging.getLogger(__name__)

OEMBED_URL = "https://www.youtube.com/oembed"
TIMEOUT = 10.0
USER_AGENT = "sen-ai/1.0 (+https://sen-ai.fr) YouTube creator mapping"

_VIDEO_ID_REGEX = re.compile(r"^[A-Za-z0-9_-]{11}$")


def is_youtube_url(url: str | None) -> bool:
    """True when the URL host looks like a YouTube property."""
    if not url:
        return False
    low = url.lower()
    return (
        "youtube.com" in low
        or low.startswith("https://youtu.be/")
        or low.startswith("http://youtu.be/")
        or "//youtu.be/" in low
    )


def extract_video_id(url: str | None) -> str | None:
    """Best-effort video ID extraction. Handles :
      - youtube.com/watch?v=ID
      - youtu.be/ID
      - youtube.com/embed/ID
      - youtube.com/shorts/ID
      - youtube.com/v/ID
    Returns the 11-char ID or None when nothing usable is found.
    """
    if not url:
        return None
    try:
        p = urlparse(url)
    except Exception:
        return None
    host = (p.netloc or "").lower()
    path = p.path or ""

    # youtu.be/<id>
    if host.endswith("youtu.be") or host == "youtu.be":
        candidate = path.lstrip("/").split("/")[0] if path else ""
        return candidate if _VIDEO_ID_REGEX.match(candidate) else None

    # youtube.com/watch?v=<id>
    if "youtube.com" in host:
        if path.startswith("/watch"):
            q = parse_qs(p.query or "")
            v = (q.get("v") or [None])[0]
            return v if v and _VIDEO_ID_REGEX.match(v) else None
        for prefix in ("/embed/", "/shorts/", "/v/", "/live/"):
            if path.startswith(prefix):
                candidate = path[len(prefix):].split("/")[0]
                return candidate if _VIDEO_ID_REGEX.match(candidate) else None
    return None


def canonical_video_url(url: str) -> str:
    """Strip tracking params + return the canonical watch URL when an ID
    is extractable. Falls back to host/path-only stripping otherwise."""
    vid = extract_video_id(url)
    if vid:
        return f"https://www.youtube.com/watch?v={vid}"
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")
    except Exception:
        return url


def fetch_oembed(video_url: str) -> dict:
    """Fetch oEmbed metadata for a YouTube URL. Returns :
        {
          status: int|None,
          error: str|None,
          title: str|None,
          author_name: str|None,
          author_url: str|None,
          thumbnail_url: str|None,
        }
    A non-200 HTTP status is reported in `status` ; the helper never
    raises on network or parse failure.
    """
    out: dict = {
        "status": None, "error": None,
        "title": None, "author_name": None, "author_url": None,
        "thumbnail_url": None,
    }
    qs = urlencode({"url": video_url, "format": "json"})
    try:
        with httpx.Client(
            timeout=TIMEOUT,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        ) as client:
            r = client.get(f"{OEMBED_URL}?{qs}")
            out["status"] = r.status_code
            if r.status_code != 200:
                # 401 = age-restricted ; 404 = deleted/private ; 403 = blocked
                if r.status_code in (401, 403, 404):
                    out["error"] = "unavailable"
                else:
                    out["error"] = f"http_{r.status_code}"
                return out
            data = r.json()
    except httpx.TimeoutException:
        out["error"] = "timeout"
        return out
    except httpx.HTTPError as e:
        out["error"] = f"network: {type(e).__name__}"
        return out
    except ValueError:
        out["error"] = "parse"
        return out

    out["title"] = (data.get("title") or "").strip() or None
    out["author_name"] = (data.get("author_name") or "").strip() or None
    out["author_url"] = (data.get("author_url") or "").strip() or None
    out["thumbnail_url"] = (data.get("thumbnail_url") or "").strip() or None
    return out


def channel_handle_from_url(channel_url: str | None) -> str | None:
    """Pull the `@handle` from a channel URL when present, else None.
    Returns None for legacy `/channel/UC...` URLs (no human handle)."""
    if not channel_url:
        return None
    try:
        p = urlparse(channel_url)
    except Exception:
        return None
    path = (p.path or "").lstrip("/")
    if path.startswith("@"):
        return path.split("/")[0]
    return None
