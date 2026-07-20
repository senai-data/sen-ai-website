"""URL normalization + citation matching for the Placements module.

PARITÉ obligatoire : this file exists as worker/services/url_matching.py AND
api/services/url_matching.py and MUST stay byte-identical (same rule as
models.py). Pure functions only - no DB, no network. Redirect resolution is
injected by the caller (worker/handlers/match_placements.py).

Ported from worker/seo_llm/src/pr_source_matcher.py with two bug fixes :
  - "www." was stripped with str.replace() anywhere in the host (corrupting
    e.g. awww.site.com -> asite.com). Now prefix-only.
  - ALL query params were dropped, so /article.php?id=123 matched ?id=456.
    Now significant (non-tracking) params are kept in the canonical form and
    the variant tier is blocked when the placement carries significant params.

Match tiers (one per (placement, citation) pair, best wins) :
  exact   - canonical forms equal (scheme/www/case/port/trailing-dot/slash/
            fragment/index-file/percent-encoding+NFC normalized, tracking
            params stripped, remaining params sorted).
  variant - path_key equal (also folds m./amp. subdomains and a trailing
            /amp path segment) AND the placement has no significant params.
  prefix  - same host, one normalized path is a strict prefix of the other,
            shorter path >= PREFIX_MATCH_MIN_PATH chars. Surfaces citations
            of truncated URLs (observed in prod). Excluded from headline
            counts by the consumer.
  domain  - registrable domains equal. "The media is cited, not your
            article" signal - aggregated only, never a hit row.

Test vectors : worker/tests/test_url_matching.py (18 cases). They MUST pass
before any deploy touching this file.
"""

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

# Tracking params dropped from the canonical form. Prefixes cover utm_*
# (Google Analytics, also utm_source=chatgpt.com stamped by ChatGPT) and
# at_* (AT Internet, common on French press sites).
TRACKING_PARAM_PREFIXES = ("utm_", "at_")
TRACKING_PARAMS = {
    "gclid", "gbraid", "wbraid", "fbclid", "msclkid", "mc_cid", "mc_eid",
    "igshid", "ysclid", "srsltid", "xtor", "cmpid", "ito", "spm", "ved",
    "usg", "sca_esv", "ref", "ref_src", "feature", "share", "amp",
    "outputtype",
}

# Subdomain prefixes folded to the apex for the variant tier (same content
# served on a mirror host). Applied repeatedly, prefix-only.
FOLDED_SUBDOMAIN_PREFIXES = ("www.", "m.", "amp.")

# LLM grounding redirect hosts. Citations on these hosts mask the real
# source URL ; the matcher resolves them (Location header only) through
# url_redirect_cache before matching.
REDIRECT_HOSTS = {"vertexaisearch.cloud.google.com"}

# Composite TLDs for the registrable-domain heuristic (no tldextract
# dependency - deliberate, keeps both docker images unchanged).
COMPOSITE_TLDS = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "org.au",
    "com.br", "com.mx", "com.ar", "co.jp", "or.jp", "co.kr", "com.cn",
    "com.tw", "co.in", "co.nz", "com.sg", "com.hk", "co.za", "com.tr",
}

# Punctuation LLM text extraction leaves glued to URLs (sentence periods,
# markdown brackets, French quotes, ellipsis from truncation).
_STRIP_EDGE_CHARS = " \t\r\n<>\"'«»()[],.…"

_INDEX_SUFFIXES = ("/index.html", "/index.htm", "/index.php", "/index.asp")

# Minimum normalized-path length for the prefix tier. Press-article slugs
# are long ; short paths prefix-matching would be noise.
PREFIX_MATCH_MIN_PATH = 40

# Plausible public hostname : dotted labels, ascii letters/digits/hyphens.
# Rejects garbage inputs ("not a url"), bare words (localhost) and hosts
# with spaces/underscores - placements are public press URLs by definition.
_HOST_RE = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)


def _clean_host(hostname):
    """Lowercased hostname without port, trailing dot, IDNA-canonicalized."""
    host = (hostname or "").strip().lower().rstrip(".")
    if not host:
        return ""
    try:
        host = host.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        pass
    return host


def _strip_www(host):
    return host[4:] if host.startswith("www.") else host


def _fold_host(host):
    """Strip www./m./amp. prefixes repeatedly (variant-tier host)."""
    changed = True
    while changed:
        changed = False
        for prefix in FOLDED_SUBDOMAIN_PREFIXES:
            if host.startswith(prefix) and len(host) > len(prefix):
                host = host[len(prefix):]
                changed = True
    return host


def _normalize_path(path):
    """Percent-decode once, NFC, lowercase, collapse //, strip index files
    and trailing slash."""
    path = unquote(path or "")
    path = unicodedata.normalize("NFC", path)
    path = path.lower()
    while "//" in path:
        path = path.replace("//", "/")
    for suffix in _INDEX_SUFFIXES:
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return path.rstrip("/")


def _path_key_path(path):
    """Variant-tier path : also folds a trailing /amp segment."""
    if path.endswith("/amp"):
        path = path[:-4]
    return path


def _significant_params(query):
    """Sorted (key, value) pairs with tracking params removed."""
    kept = []
    for key, value in parse_qsl(query or "", keep_blank_values=True):
        k = key.lower()
        if k.startswith(TRACKING_PARAM_PREFIXES) or k in TRACKING_PARAMS:
            continue
        kept.append((k, value))
    kept.sort()
    return tuple(kept)


def normalize_url(url):
    """Normalize a URL for matching.

    Returns a dict :
      canonical          - https://{host-no-www}{path}[?sorted-params]
      path_key           - {folded-host}{path-no-amp} (query ignored)
      host               - www-stripped host
      registrable_domain - eTLD+1 heuristic
      significant_params - tuple of kept (key, value) pairs
      parse_error        - True when urlsplit failed (fallback fields set)
    """
    raw = (url or "").strip().strip(_STRIP_EDGE_CHARS)
    result = {
        "canonical": "",
        "path_key": "",
        "host": "",
        "registrable_domain": "",
        "significant_params": (),
        "parse_error": False,
    }
    if not raw:
        result["parse_error"] = True
        return result
    if "://" not in raw:
        raw = "https://" + raw
    host = ""
    parsed = None
    try:
        parsed = urlsplit(raw)
        host = _clean_host(parsed.hostname)
    except ValueError:
        parsed = None
    if host and not _HOST_RE.match(host):
        host = ""
    if parsed is None or not host:
        fallback = raw.lower().rstrip("/")
        result["canonical"] = fallback
        result["path_key"] = fallback
        result["parse_error"] = True
        return result

    host_no_www = _strip_www(host)
    path = _normalize_path(parsed.path)
    params = _significant_params(parsed.query)

    canonical = "https://" + host_no_www + path
    if params:
        canonical += "?" + urlencode(params)

    result["canonical"] = canonical
    result["path_key"] = _fold_host(host) + _path_key_path(path)
    result["host"] = host_no_www
    result["registrable_domain"] = registrable_domain(host)
    result["significant_params"] = params
    return result


def registrable_domain(host):
    """eTLD+1 heuristic : last 2 labels, or 3 when the last 2 form a known
    composite TLD (co.uk, com.au, ...)."""
    labels = [label for label in (host or "").split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels)
    if ".".join(labels[-2:]) in COMPOSITE_TLDS:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def is_redirect_url(url):
    """True when the URL lives on an LLM grounding redirect host."""
    try:
        host = _clean_host(urlsplit(url or "").hostname)
    except ValueError:
        return False
    return host in REDIRECT_HOSTS


def url_hash(url):
    """Stable cache key for url_redirect_cache (redirect URLs are ~250 chars)."""
    return hashlib.sha256((url or "").encode("utf-8")).hexdigest()


def build_index(placements):
    """O(1) lookup index over placements.

    placements : iterable of dicts with at least {id, url}. URLs are
    re-normalized here so index and storage always agree with the current
    normalization rules.
    """
    index = {
        "canonical": {},
        "path_key": {},
        "host_paths": {},
        "domain": {},
    }
    for placement in placements:
        norm = normalize_url(placement["url"])
        if norm["parse_error"] and not norm["host"]:
            continue
        pid = placement["id"]
        index["canonical"].setdefault(norm["canonical"], []).append(pid)
        # variant tier is blocked when the placement carries significant
        # params (the bug-fix : /article.php?id=123 vs ?id=456).
        if not norm["significant_params"]:
            index["path_key"].setdefault(norm["path_key"], []).append(pid)
        path = norm["canonical"][len("https://" + norm["host"]):].split("?")[0]
        index["host_paths"].setdefault(norm["host"], []).append((pid, path))
        index["domain"].setdefault(norm["registrable_domain"], []).append(pid)
    return index


def match_citation(index, citation_url):
    """Match one citation URL against the placement index.

    Returns a list of (placement_id, match_level) with at most one level per
    placement : exact > variant > prefix > domain.
    """
    norm = normalize_url(citation_url)
    if norm["parse_error"] and not norm["host"]:
        return []

    matches = {}
    for pid in index["canonical"].get(norm["canonical"], []):
        matches[pid] = "exact"
    for pid in index["path_key"].get(norm["path_key"], []):
        matches.setdefault(pid, "variant")

    cite_path = norm["canonical"][len("https://" + norm["host"]):].split("?")[0]
    for pid, placement_path in index["host_paths"].get(norm["host"], []):
        if pid in matches:
            continue
        shorter, longer = sorted((placement_path, cite_path), key=len)
        if (
            len(shorter) >= PREFIX_MATCH_MIN_PATH
            and shorter != longer
            and longer.startswith(shorter)
        ):
            matches[pid] = "prefix"

    for pid in index["domain"].get(norm["registrable_domain"], []):
        matches.setdefault(pid, "domain")

    return list(matches.items())
