"""Wikipedia REST + Action API client - brand entity presence check.

Sprint 4 (project_10_action_features.md #1). ChatGPT cites Wikipedia 48% of the
time (Stackmatix 30M citations study, mai 2026). If a brand has no proper
Wikipedia page, it's structurally invisible in the single most-cited source on
the planet.

This adapter is intentionally minimal :

    check_brand_wikipedia(brand_name, langs=["fr", "en"]) -> dict

returns one entry per language with `exists`, `url`, `title`, `extract`,
`last_modified`, `references_count`, `quality_score`. Both the REST summary
endpoint and the Action API are public, no auth, free.

Failures are non-fatal - a network error returns `exists=False` with an
`error` field so the caller can decide to retry later (the TTL on the cached
JSONB column is 7 days, so a transient error won't poison the cache for long).

Rate-limit : Wikipedia recommends ≤ 200 req/s, with User-Agent identifying
the project. We set a polite UA + cap parallelism at the handler level.
"""
from __future__ import annotations

import logging
import re
import time
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import Iterable

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "sen-ai/1.0 (https://sen-ai.fr; contact@sen-ai.fr) Wikipedia presence audit"
TIMEOUT = 10.0
# Wikipedia rate-limits anonymous requests aggressively (HTTP 429 around
# ~5-10 req/s per IP on the Action API). One retry with backoff is enough to
# survive a transient hit ; the handler also throttles brand-to-brand.
RETRY_ON_429 = 2
RETRY_BACKOFF_SECONDS = 1.5

# Sprint 4.5 - Confidence thresholds to filter Wikipedia false positives.
# Wikipedia opensearch is intentionally fuzzy : 'Keracnyl' returns 'Keracyanin'
# (a chemical), 'Anthelios' returns 'Anthemius' (a Roman emperor). The hit
# rate looks great on paper but the user clicks through to junk pages.
# We accept a candidate only when the title or extract is "close enough" to
# the brand name.
TITLE_SIMILARITY_HIGH = 0.85   # >= : confident match, no extra check needed
TITLE_SIMILARITY_MIN = 0.55    # < : reject outright unless extract contains the brand

# Sprint 4.6 - Description-based entity-type filter. Wikipedia REST `summary`
# exposes a one-line description like "page d'homonymie de Wikimedia" or
# "French writer". When the description clearly identifies the page as a
# disambiguation, a person, a place, or another non-brand entity, we reject
# the candidate even when the title matches the brand name.
#
# Word-boundary matching keeps "Saint Laurent" (a brand) from being killed by
# the "saint" entry, and "Brand New" (a band) from sneaking through "brand"
# in the allowlist.
DESC_BLOCKLIST = (
    # English persons
    r"writer", r"novelist", r"poet", r"playwright", r"essayist", r"journalist",
    r"politician", r"diplomat", r"general", r"officer", r"soldier",
    r"footballer", r"basketball player", r"baseball player", r"tennis player",
    r"actor", r"actress", r"singer", r"musician", r"rapper", r"composer",
    r"scientist", r"philosopher", r"mathematician", r"physicist", r"biologist",
    r"chemist", r"geneticist", r"physician", r"surgeon", r"lawyer", r"judge",
    r"painter", r"sculptor", r"architect", r"filmmaker",
    r"pope", r"king", r"queen", r"duke", r"duchess", r"emperor", r"empress",
    # French persons
    r"écrivaine?", r"romanci[èe]re?", r"po[èe]tesse?", r"po[èe]te",
    r"homme politique", r"femme politique", r"diplomate",
    r"footballeu(?:r|se)", r"acteur", r"actrice", r"chanteu(?:r|se)",
    r"musicien(?:ne)?", r"rappeu(?:r|se)", r"compositeu(?:r|se)",
    r"scientifique", r"philosophe", r"mathématicien(?:ne)?",
    r"physicien(?:ne)?", r"biologiste", r"chimiste", r"médecin",
    r"peintre", r"sculpteu(?:r|se)", r"architecte", r"réalisateu(?:r|se)",
    r"pape", r"empereu(?:r|se)", r"impératrice",
    # Places
    r"village", r"commune", r"city", r"town", r"ville",
    r"municipality", r"municipalité",
    r"river", r"rivière", r"fleuve", r"mountain", r"montagne",
    r"lake", r"lac", r"country", r"pays", r"département", r"province",
    # Other non-brand entities
    r"film", r"movie", r"television series", r"série télévisée",
    r"novel", r"roman", r"song", r"chanson", r"album", r"play",
    r"battle", r"bataille", r"war", r"guerre", r"treaty", r"traité",
    r"chemical compound", r"composé chimique", r"molecule", r"molécule",
    # Disambiguation
    r"page d'homonymie", r"disambiguation page",
    r"wikimedia disambiguation", r"homonymie",
)
DESC_ALLOWLIST = (
    r"brand", r"marque",
    r"company", r"entreprise", r"société",
    r"corporation", r"firm",
    r"laboratory", r"laboratoire", r"labs",
    r"pharmaceutical", r"pharmaceutique", r"pharma",
    r"skincare", r"cosmetic", r"cosmétique", r"cosmetics",
    r"product line", r"gamme",
    r"trademark", r"marque déposée",
    r"subsidiary", r"filiale",
    r"manufacturer", r"fabricant",
)

_BLOCK_RE = re.compile(r"\b(" + "|".join(DESC_BLOCKLIST) + r")\b", re.IGNORECASE | re.UNICODE)
_ALLOW_RE = re.compile(r"\b(" + "|".join(DESC_ALLOWLIST) + r")\b", re.IGNORECASE | re.UNICODE)


def _description_signal(description: str | None) -> tuple[str | None, str | None]:
    """Classify a Wikipedia one-line description.

    Returns (signal, matched_term) where signal is :
      - "blocked"  → the page is plainly not a brand (person, place, film…).
      - "allowed"  → the description names a brand-like entity type.
      - None       → ambiguous, defer to title similarity.

    `matched_term` is the regex hit, useful for the UI to explain WHY we
    rejected or accepted the candidate.
    """
    if not description:
        return (None, None)
    m_block = _BLOCK_RE.search(description)
    if m_block:
        return ("blocked", m_block.group(1))
    m_allow = _ALLOW_RE.search(description)
    if m_allow:
        return ("allowed", m_allow.group(1))
    return (None, None)


# Sprint 4.6 - Wikidata instance_of (P31) whitelist / blacklist for cases the
# description heuristic can't settle. Resolved via wbgetclaims, one extra call
# per ambiguous candidate.
WIKIDATA_BRAND_QIDS = {
    "Q4830453",   # business
    "Q43229",     # organization
    "Q783794",    # company
    "Q431289",    # brand
    "Q167270",    # trademark
    "Q186313",    # laboratory
    "Q2424752",   # product
    "Q2401749",   # cosmetic brand
    "Q12136",     # disease - keep out actually
    "Q11410",     # game (some brand cases)
    "Q1135857",   # holding company
    "Q49265373",  # pharmaceutical company
    "Q1664720",   # institute
    "Q4830453",   # business enterprise (dup)
    "Q6881511",   # enterprise
}
WIKIDATA_BRAND_QIDS.discard("Q12136")  # disease - guard, never want this

WIKIDATA_NONBRAND_QIDS = {
    "Q5",          # human
    "Q4167410",    # Wikimedia disambiguation page
    "Q486972",     # human settlement
    "Q515",        # city
    "Q3957",       # town
    "Q15284",      # municipality
    "Q11424",      # film
    "Q47461344",   # written work
    "Q571",        # book
    "Q7725634",    # literary work
    "Q4438121",    # sports tournament
    "Q3624078",    # sovereign state
    "Q23397",      # lake
    "Q34442",      # road
    "Q4022",       # river
    "Q8502",       # mountain
}


def _wikidata_instance_of(qid: str) -> tuple[str | None, list[str]]:
    """Resolve a Wikidata Q-id to its `instance of` (P31) values.

    Returns (verdict, qids) where verdict is :
      - "brand"     → at least one P31 value is in the brand-like whitelist.
      - "non_brand" → at least one P31 is on the blacklist AND none on the whitelist.
      - None        → no P31 found or no list hit.

    `qids` is the raw list of P31 values for debugging / future tuning.
    """
    if not qid or not qid.startswith("Q"):
        return (None, [])
    url = "https://www.wikidata.org/w/api.php"
    params = {
        "action": "wbgetclaims",
        "entity": qid,
        "property": "P31",
        "format": "json",
    }
    try:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as c:
            r = _get_with_backoff(c, url, params)
            r.raise_for_status()
            claims = r.json().get("claims", {}).get("P31", [])
    except Exception as e:  # noqa: BLE001
        logger.info(f"wikidata P31 lookup failed for {qid}: {e}")
        return (None, [])

    p31_qids: list[str] = []
    for claim in claims:
        snak = claim.get("mainsnak", {})
        if snak.get("datatype") != "wikibase-item":
            continue
        v = (snak.get("datavalue") or {}).get("value") or {}
        target = v.get("id")
        if target:
            p31_qids.append(target)

    if any(q in WIKIDATA_BRAND_QIDS for q in p31_qids):
        return ("brand", p31_qids)
    if any(q in WIKIDATA_NONBRAND_QIDS for q in p31_qids):
        return ("non_brand", p31_qids)
    return (None, p31_qids)


def _normalize_for_match(s: str) -> str:
    """Lowercase + strip accents + collapse non-alphanumerics to single spaces.

    Brand names and Wikipedia titles often differ in trivial ways (case,
    diacritics, hyphenation). Normalizing both sides lets `SequenceMatcher`
    score them on real semantic distance rather than punctuation noise.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9]+", " ", s).strip().lower()
    return re.sub(r"\s+", " ", s)


def _confidence(brand_name: str, title: str, extract: str | None) -> tuple[str, float]:
    """Decide whether a Wikipedia candidate is the brand we asked about.

    Returns (label, similarity) where label is :
      - "match"      : strong title similarity OR brand name appears verbatim
                        in title / extract.
      - "low"        : weak title match but plausible (kept, UI flags it).
      - "reject"     : not the brand. Caller should treat as exists=False.

    The function never throws - when in doubt it leans toward "low" so the
    user still gets to see and judge.
    """
    nbrand = _normalize_for_match(brand_name)
    ntitle = _normalize_for_match(title)
    nextract = _normalize_for_match(extract or "")
    if not nbrand or not ntitle:
        return "reject", 0.0

    sim = SequenceMatcher(None, nbrand, ntitle).ratio()
    # Substring evidence beats fuzzy ratio. A brand whose normalized name
    # appears literally in the Wikipedia title is the brand, even if the
    # title carries qualifiers like 'Avène (laboratoire)' that drag the
    # ratio down.
    title_contains_brand = (nbrand in ntitle) or (ntitle in nbrand)
    extract_contains_brand = nbrand in nextract

    if sim >= TITLE_SIMILARITY_HIGH or title_contains_brand:
        return "match", sim
    if sim >= TITLE_SIMILARITY_MIN and extract_contains_brand:
        return "match", sim
    if sim >= TITLE_SIMILARITY_MIN:
        return "low", sim
    if extract_contains_brand:
        # Title looks unrelated but the extract mentions the brand - usually a
        # disambiguation or a "related concepts" page. Keep as low-confidence.
        return "low", sim
    return "reject", sim


def _get_with_backoff(client: httpx.Client, url: str, params: dict | None = None) -> httpx.Response:
    """GET that swallows a single 429 with a polite sleep. Anything else
    raises so the caller can surface the error per-language."""
    for attempt in range(RETRY_ON_429 + 1):
        r = client.get(url, params=params)
        if r.status_code == 429 and attempt < RETRY_ON_429:
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
            continue
        return r
    return r


def _now_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _opensearch(query: str, lang: str) -> str | None:
    """Find the best matching Wikipedia page title for a brand name.

    Wikipedia opensearch returns a tuple-shaped JSON :
        [query, [titles], [descriptions], [urls]]
    We pick the first hit ; if none, return None. Fuzzy match handles
    accent/case variations (e.g., 'Avene' -> 'Avène', 'A-Derma' -> 'A-Derma').
    """
    url = f"https://{lang}.wikipedia.org/w/api.php"
    params = {
        "action": "opensearch",
        "search": query,
        "limit": 3,
        "namespace": 0,  # main namespace only (no User:/Talk:/etc.)
        "format": "json",
    }
    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as c:
        r = _get_with_backoff(c, url, params)
        r.raise_for_status()
        data = r.json()
    if not isinstance(data, list) or len(data) < 2 or not data[1]:
        return None
    return data[1][0]


def _summary(title: str, lang: str) -> dict | None:
    """Fetch REST summary : title, extract, lastrev timestamp, page URL."""
    safe_title = title.replace(" ", "_")
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{safe_title}"
    with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as c:
        r = _get_with_backoff(c, url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


def _page_info(title: str, lang: str) -> dict:
    """Fetch deeper metadata via Action API : last revision, external links,
    categories. Used to compute the quality score AND to validate that the
    brand's official domain is actually linked from the Wikipedia page
    (Sprint 4.7 domain check).

    Returns {} on failure rather than raising - caller still gets the summary.
    """
    safe_title = title.replace(" ", "_")
    url = f"https://{lang}.wikipedia.org/w/api.php"
    params = {
        "action": "query",
        "prop": "info|extlinks|categories|revisions",
        "titles": safe_title,
        "ellimit": "max",     # so we can count + scan all external references
        "cllimit": "max",
        "rvprop": "timestamp|size",
        "redirects": 1,
        "format": "json",
    }
    try:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as c:
            r = _get_with_backoff(c, url, params)
            r.raise_for_status()
            pages = r.json().get("query", {}).get("pages", {})
            page = next(iter(pages.values())) if pages else {}
    except Exception as e:  # noqa: BLE001 - non-fatal, summary is the source of truth
        logger.info(f"wikipedia page_info failed for {title}/{lang}: {e}")
        return {}

    extlinks_raw = page.get("extlinks") or []
    # Action API returns extlinks as [{"*": "https://..."} or {"url": "..."}]
    # depending on version. Normalize to a flat list of URLs for downstream.
    extlinks_urls: list[str] = []
    for el in extlinks_raw:
        if not isinstance(el, dict):
            continue
        u = el.get("url") or el.get("*")
        if u:
            extlinks_urls.append(u)
    cats = page.get("categories") or []
    rev = (page.get("revisions") or [{}])[0]
    return {
        "references_count": len(extlinks_urls),
        "categories_count": len(cats),
        "page_size_bytes": rev.get("size"),
        "last_modified": rev.get("timestamp"),
        "extlinks": extlinks_urls,
    }


def _domain_in_extlinks(brand_domain: str | None, extlinks: list[str]) -> bool:
    """Check whether the brand's registered domain appears in any extlink.

    A brand whose official site is linked from its Wikipedia page is almost
    certainly the right entity - the Wikidata Q-id check can fail (no P31, no
    sitelinks for our lang) but if Wikipedia references the brand's domain
    in `extlinks`, that's unambiguous.

    We compare the host (sans scheme, port, leading 'www.', path) so
    'https://ducray.com/fr-fr' matches an extlink 'http://www.ducray.com'.
    """
    if not brand_domain or not extlinks:
        return False
    # Extract host from brand_domain in a forgiving way (the column may store
    # 'ducray.com' or 'ducray.com/fr-fr' or even 'https://www.ducray.com/').
    host = brand_domain.strip().lower()
    host = re.sub(r"^https?://", "", host)
    host = host.split("/", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return False
    for url in extlinks:
        try:
            ulow = url.lower()
            uhost = re.sub(r"^https?://", "", ulow).split("/", 1)[0]
            if uhost.startswith("www."):
                uhost = uhost[4:]
            if uhost == host or uhost.endswith("." + host) or host.endswith("." + uhost):
                return True
        except Exception:  # noqa: BLE001 - never crash, fall through to next link
            continue
    return False


def _quality_score(extract: str | None, info: dict) -> int:
    """Composite 0-100 score : informative for an AI editor at a glance.

    Heuristic, not science. Three signals worth roughly a third each :
      - Content depth   : extract length (a stub gets ~5, a deep article ~40+)
      - Reference depth : how many external links the page has (Wikipedia
        editors treat external citations as a notability signal)
      - Recency         : was the page edited in the last 12 months ?
    """
    score = 0
    if extract:
        score += min(40, len(extract) // 30)
    refs = info.get("references_count") or 0
    score += min(30, refs * 2)
    last_mod = info.get("last_modified") or ""
    m = re.match(r"^(\d{4})", last_mod)
    if m:
        try:
            year = int(m.group(1))
            current_year = datetime.utcnow().year
            if year >= current_year - 1:
                score += 30
            elif year >= current_year - 3:
                score += 15
        except ValueError:
            pass
    return max(0, min(100, score))


def check_brand_wikipedia(
    brand_name: str,
    langs: Iterable[str] = ("fr", "en"),
    brand_domain: str | None = None,
) -> dict:
    """Look up the brand on Wikipedia in each requested language.

    Resolution path per language : opensearch -> first title -> REST summary
    -> Action API for refs/categories/last-mod. Anything that fails inside a
    language falls back to `exists=False` for that language only - other
    languages still run.

    Returns the structure expected by client_brands.wikipedia JSONB :

        {
            "checked_at": "2026-05-27T11:00:00Z",
            "by_lang": {
                "fr": {"exists": True, "url": "...", ...} | {"exists": False, "error": "..."},
                "en": ...
            }
        }
    """
    out: dict = {"checked_at": _now_iso(), "by_lang": {}}
    if not brand_name or not brand_name.strip():
        return out

    for lang in langs:
        try:
            title = _opensearch(brand_name, lang)
            if not title:
                out["by_lang"][lang] = {"exists": False, "reason": "no_match"}
                continue
            summary = _summary(title, lang)
            if not summary:
                out["by_lang"][lang] = {"exists": False, "reason": "summary_404"}
                continue
            info = _page_info(title, lang)
            extract = summary.get("extract") or ""
            resolved_title = summary.get("title") or title
            description = summary.get("description") or ""
            page_type = summary.get("type") or "standard"
            wikibase_item = summary.get("wikibase_item")

            # Sprint 4.6 #1 - kill disambiguation pages outright. They have
            # the same title as the brand by design, so the fuzzy similarity
            # gate alone never catches them.
            if page_type == "disambiguation":
                out["by_lang"][lang] = {
                    "exists": False,
                    "reason": "disambiguation_page",
                    "rejected_title": resolved_title,
                    "description": description,
                }
                continue

            # Sprint 4.6 #2 - description-based entity type gate. "French
            # writer", "page d'homonymie", "commune française" etc. all
            # flag the page as plainly not a brand even when the title
            # contains the brand name.
            desc_signal, desc_term = _description_signal(description)
            if desc_signal == "blocked":
                out["by_lang"][lang] = {
                    "exists": False,
                    "reason": "wrong_entity_type",
                    "rejected_title": resolved_title,
                    "description": description,
                    "blocked_term": desc_term,
                }
                continue

            # Sprint 4.5 - title-similarity / extract-substring gate.
            confidence_label, sim_ratio = _confidence(brand_name, resolved_title, extract)
            if confidence_label == "reject":
                out["by_lang"][lang] = {
                    "exists": False,
                    "reason": "fuzzy_mismatch",
                    "rejected_title": resolved_title,
                    "title_similarity": round(sim_ratio, 2),
                }
                continue

            # Description allowlist gives us a positive signal - upgrade
            # any "low" confidence to "match" when the description itself
            # says the entity is a brand.
            if desc_signal == "allowed" and confidence_label == "low":
                confidence_label = "match"

            # Sprint 4.6 #3 - Wikidata fallback : when the title is ambiguous
            # (confidence=low) and the description didn't settle it, look up
            # the entity's `instance of` (P31) values. Brand-like → upgrade.
            # Person/place-like → reject.
            wikidata_p31 = None
            if confidence_label == "low" and wikibase_item:
                verdict, p31_qids = _wikidata_instance_of(wikibase_item)
                wikidata_p31 = p31_qids[:5]
                if verdict == "non_brand":
                    out["by_lang"][lang] = {
                        "exists": False,
                        "reason": "wikidata_non_brand",
                        "rejected_title": resolved_title,
                        "description": description,
                        "wikibase_item": wikibase_item,
                        "wikidata_p31": wikidata_p31,
                    }
                    continue
                if verdict == "brand":
                    confidence_label = "match"

            # Sprint 4.7 - domain validation. When we know the brand's
            # official website (client_brands.domain) and the Wikipedia page
            # links to that domain via `extlinks`, we have an almost-certain
            # match (Wikipedia editors only put a brand's own URL on the
            # brand's actual page). The reverse - domain known, but absent
            # from extlinks - strongly suggests a name collision.
            domain_match: bool | None = None
            if brand_domain:
                domain_match = _domain_in_extlinks(brand_domain, info.get("extlinks") or [])
                if domain_match:
                    confidence_label = "match"
                elif confidence_label == "match":
                    # We had a strong title hit but the brand's own site
                    # isn't referenced anywhere - demote to "low" so the
                    # UI flags it for verification rather than asserting
                    # certainty.
                    confidence_label = "low"

            out["by_lang"][lang] = {
                "exists": True,
                "title": resolved_title,
                "url": (summary.get("content_urls") or {}).get("desktop", {}).get("page")
                       or f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}",
                "extract": extract,
                "description": description,
                "thumbnail": (summary.get("thumbnail") or {}).get("source"),
                "last_modified": info.get("last_modified") or summary.get("timestamp"),
                "references_count": info.get("references_count"),
                "categories_count": info.get("categories_count"),
                "page_size_bytes": info.get("page_size_bytes"),
                "quality_score": _quality_score(extract, info),
                "confidence": confidence_label,        # 'match' | 'low'
                "title_similarity": round(sim_ratio, 2),
                "page_type": page_type,
                "wikibase_item": wikibase_item,
                "wikidata_p31": wikidata_p31,
                "desc_signal": desc_signal,
                "desc_match": desc_term,
                "domain_match": domain_match,           # True / False / None
            }
        except httpx.HTTPError as e:
            logger.warning(f"wikipedia check failed for {brand_name}/{lang}: {e}")
            out["by_lang"][lang] = {"exists": False, "error": str(e)[:200]}
        except Exception as e:  # noqa: BLE001 - never crash the worker
            logger.exception(f"wikipedia check crashed for {brand_name}/{lang}")
            out["by_lang"][lang] = {"exists": False, "error": str(e)[:200]}
    return out
