"""URL filter — universal cross-vertical denylist for content generation.

Used by content generation pipelines (FAQ, future article gen) to drop
web_search URLs that are NEVER acceptable citations regardless of the
client's vertical : e-commerce / cart / affiliate paths, shopping
aggregators, social media. These are domain-agnostic patterns — the same
rules apply for dermo-cosmetic, automotive, finance, B2B SaaS.

Combined with the per-scan `competitor_domains` denylist (see
`services.competitor_domains`), this gives a complete HARD filter for
brand-bias defense :

  is_excluded_url(url, competitor_domains)
    = True if URL is on a competitor brand domain
      OR URL is an e-commerce / cart / affiliate path
      OR URL is on a known shopping aggregator domain
      OR URL is on a social media platform
    = False otherwise (URL passes — let the LLM cite it freely)

The OPPOSITE of an allowlist : we don't qualify "trusted" sources here,
we only exclude "definitely not citation-worthy" ones. Trust comes from
`services.trust_sources` which is a SOFT prefer-signal injected in the
LLM's search prompt, not a filter.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


# E-commerce / cart / affiliate path patterns. Substring matched against the
# full URL (path + query). Cross-vertical : same rules apply for retail,
# dermo, B2B SaaS demo trials, automotive parts shops, etc.
_ECOMMERCE_PATH_PATTERNS: tuple[str, ...] = (
    "/produit/", "/produits/", "/product/", "/products/",
    "/shop/", "/shopping/", "/store/",
    "/cart/", "/panier/", "/basket/",
    "/buy/", "/order/", "/checkout/",
    "/commande/", "/achat/",
    "/add-to-cart", "/add-to-basket",
    "/p/",  # common product page pattern (e.g., amazon.fr/dp/p/...)
)


# Query-string patterns indicating affiliate / e-commerce intent.
_ECOMMERCE_QUERY_PATTERNS: tuple[str, ...] = (
    "add-to-cart=", "add_to_cart=",
    "affid=", "affiliate=", "aff_id=", "affref=",
    "ref=affiliate", "ref=aff_",
    "utm_medium=affiliate", "utm_source=affiliate",
    "tag=", "tag_id=",  # Amazon-style affiliate tag (broad — false positive risk on /tag/ pages, but path check handles those)
)


# Subdomain prefixes that signal shopping / retail surfaces.
_ECOMMERCE_SUBDOMAINS: tuple[str, ...] = (
    "shop.", "store.", "boutique.", "buy.",
    "basket.", "panier.", "cart.",
)


# Known shopping aggregator / e-commerce platform domains. These don't host
# authoritative reference content — they host listings. Drop universally.
_ECOMMERCE_DOMAINS: tuple[str, ...] = (
    "amazon.", "amzn.",
    "cdiscount.", "fnac.", "darty.",
    "ebay.", "alibaba.", "aliexpress.",
    "etsy.", "rakuten.",
    "leboncoin.",
    "shopify.com",
)


# Social media platform domains — never authoritative for content gen.
_SOCIAL_DOMAINS: tuple[str, ...] = (
    "facebook.", "fb.com",
    "twitter.", "x.com",
    "instagram.",
    "youtube.", "youtu.be",
    "linkedin.",
    "tiktok.",
    "pinterest.",
    "reddit.",
    "snapchat.",
    "discord.",
)


# Generic blog / forum / opinion subdomains. Less hard-and-fast than the
# others (some "blog.regulator.gov" pages might be authoritative), but for
# v1 we keep the same rule as seo_llm's _discover_reference_sources : drop
# anything that signals user-generated or opinion content.
_BLOG_FORUM_SUBDOMAINS: tuple[str, ...] = (
    "blog.", "blogs.", "forum.", "forums.",
    "avis.", "reviews.", "opinion.",
)


def _normalize_domain(raw: str) -> str:
    """Lowercase, strip protocol + www + path. Return '' on garbage input."""
    if not raw or not isinstance(raw, str):
        return ""
    d = raw.strip().lower()
    d = re.sub(r"^https?://", "", d)
    if d.startswith("www."):
        d = d[4:]
    d = d.split("/", 1)[0].rstrip(".")
    return d if "." in d else ""


def _url_to_domain_and_pq(url: str) -> tuple[str, str]:
    """Parse a URL into (bare_domain, path+query_lowercased).

    Returns ('', '') on garbage input. Used by is_excluded_url to avoid
    repeated parsing inside the hot loop.
    """
    if not url or not isinstance(url, str):
        return "", ""
    try:
        p = urlparse(url)
        netloc = (p.netloc or "").lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path_query = (p.path or "") + ("?" + p.query if p.query else "")
        return netloc, path_query.lower()
    except Exception:
        return "", ""


def _domain_matches_set(domain: str, denylist: set[str] | tuple[str, ...]) -> bool:
    """True if `domain` exact-matches or is a subdomain of any entry in `denylist`.

    Entries in the denylist are assumed pre-normalized (lowercase, no www).
    """
    if not domain:
        return False
    for entry in denylist:
        if not entry:
            continue
        if domain == entry or domain.endswith("." + entry):
            return True
    return False


def is_excluded_url(url: str, competitor_domains: set[str] | None = None) -> tuple[bool, str]:
    """Decide if a URL should be hard-dropped from content generation citations.

    Returns (excluded: bool, reason: str). The reason is for diagnostic
    logging — callers can aggregate counts by reason ('competitor',
    'ecommerce_path', 'ecommerce_domain', 'social', 'blog_forum', '').

    Order of checks is meaningful : competitor takes precedence (it's the
    primary strategic differentiator), then e-commerce, then social, then
    blog/forum.
    """
    netloc, path_query = _url_to_domain_and_pq(url)
    if not netloc:
        return False, ""  # Can't parse → leave decision to caller (usually keep)

    # 1. Per-scan competitor brand domains — HARD constraint
    if competitor_domains and _domain_matches_set(netloc, competitor_domains):
        return True, "competitor"

    # 2. Shopping aggregator / e-commerce platform domains
    for ecom in _ECOMMERCE_DOMAINS:
        if ecom.endswith("."):
            if netloc.startswith(ecom) or ("." + ecom) in ("." + netloc + "."):
                return True, "ecommerce_domain"
        else:
            if netloc == ecom or netloc.endswith("." + ecom):
                return True, "ecommerce_domain"

    # 3. E-commerce subdomain prefix (shop., store., etc.)
    for sub in _ECOMMERCE_SUBDOMAINS:
        if netloc.startswith(sub):
            return True, "ecommerce_subdomain"

    # 4. E-commerce path patterns (/produit/, /shop/, /cart/, …)
    for path in _ECOMMERCE_PATH_PATTERNS:
        if path in path_query:
            return True, "ecommerce_path"

    # 5. Affiliate / cart query strings
    for q in _ECOMMERCE_QUERY_PATTERNS:
        if q in path_query:
            return True, "ecommerce_query"

    # 6. Social media platforms
    for soc in _SOCIAL_DOMAINS:
        if soc.endswith("."):
            if netloc.startswith(soc):
                return True, "social"
        else:
            if netloc == soc or netloc.endswith("." + soc):
                return True, "social"

    # 7. Blog / forum / opinion subdomains
    for bf in _BLOG_FORUM_SUBDOMAINS:
        if netloc.startswith(bf):
            return True, "blog_forum"

    return False, ""


def partition_urls(urls: list[str],
                   competitor_domains: set[str] | None = None,
                   ) -> tuple[list[str], dict[str, list[str]]]:
    """Split a list of URLs into (kept, dropped_by_reason).

    Returns :
      kept : urls that pass the filter
      dropped_by_reason : {reason: [url, ...]} for diagnostic logging.
        Empty reasons are not present in the dict.

    Used by content generation handlers to log "kept N / dropped M by
    reason ..." in one structured line per generation.
    """
    kept: list[str] = []
    dropped: dict[str, list[str]] = {}
    for u in urls or []:
        excluded, reason = is_excluded_url(u, competitor_domains)
        if excluded:
            dropped.setdefault(reason, []).append(u)
        else:
            kept.append(u)
    return kept, dropped


def format_drop_summary(dropped_by_reason: dict[str, list[str]]) -> str:
    """One-line human-readable summary of drops for log lines.

    Example output : 'competitor=2, ecommerce_path=1, social=1'
    """
    if not dropped_by_reason:
        return "no drops"
    parts = [f"{reason}={len(urls)}" for reason, urls in sorted(dropped_by_reason.items())]
    return ", ".join(parts)
