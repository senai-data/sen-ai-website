"""FAQPageMatcher subclass that skips a user-provided URL exclusion list.

When the user clicks "Find a different page" on the validation page, we want
to re-run the matcher but reject any URL it has already proposed (and the
user has dismissed). The seo_llm submodule's FAQPageMatcher has no native
exclusion knob — we plug into `_validate_url`, the gate every candidate URL
passes through before entering the `candidates` list, regardless of which
search provider (Serper or OpenAI web_search) is active.

Submodule code is read-only by convention (feedback_reuse_code) — this
wrapper is the SaaS-side specialization point.
"""

from urllib.parse import urlparse

from seo_llm.src.faq_page_matcher import FAQPageMatcher


def _normalize_url(url: str) -> str:
    """Lower-case host (strip www.) + path without trailing slash, no query.

    Two URLs are 'the same page' for the exclusion check when their
    normalized form matches. Query strings + fragments are dropped because
    tracking params already got stripped upstream in materialize_content_items.
    """
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        host = (parsed.netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = (parsed.path or "").rstrip("/")
        return f"{host}{path}"
    except Exception:
        return url.strip().lower()


class ExcludingFAQPageMatcher(FAQPageMatcher):
    def __init__(self, *args, exclude_urls: list[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._excluded_normalized: set[str] = {
            _normalize_url(u) for u in (exclude_urls or []) if u
        }

    def _validate_url(self, url: str, target_site: str) -> bool:
        # Parent enforces "must be on target_site + valid host". We then drop
        # anything the user has already rejected. Filtering here (not after)
        # keeps the candidate pool genuine — FAQ-section detection and
        # fallback ordering operate only on still-eligible URLs.
        if not super()._validate_url(url, target_site):
            return False
        return _normalize_url(url) not in self._excluded_normalized
