"""Phase MR.2 — Suggest alternative media for a netlinking_article item.

Reads the populated `media_catalog` (built nightly by discover_media_catalog)
plus live `scan_llm_results.citations` and returns a top-N ranked list of
buyable media domains the user can replace their current target_url with.

Called from `api/routers/content_items.py:POST /content-items/{id}/suggest-media`.
Sprint 2 covers sources 1-4 (DB-only, gratuit). Source 5 LLM web_search
fallback is Sprint 3 (credit-debited).

Cascade priority :
  1. same-scan citations    (highest signal — LLM said this matters HERE)
  2. cross-scan citations   (k-anonymized ≥3 distinct clients per
                             country+language to prevent inter-tenant leak)
  3. client.trust_sources   (workspace-declared authoritative domains)
  4. media_catalog          (cross-tenant aggregate from past citations)

Each candidate keeps a `sources` set and `signals` accumulator. Scoring
weights are overridable per-client via `client.apps['media_scoring_weights']`.

Output ALWAYS carries an explainability `breakdown.{reasons, risks}` — never
return a score without prose justification. See feedback_tooltips.md +
feedback_vocabulary.md.
"""

from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session

from services.intent_taxonomy import is_safety_intent

logger = logging.getLogger(__name__)


# ─── Defaults (overridable via client.apps['media_scoring_weights']) ─────

DEFAULT_WEIGHTS: dict[str, float] = {
    "llm_citation_topic":    3.0,   # normalize(citations_count_on_topic) 0..1
    "persona_audience":      2.0,   # Jaccard audience tags ∩ persona — Sprint 3 (stubbed)
    "editorial_voice_match": 1.5,   # text similarity — Sprint 3 (stubbed)
    "authority":             1.0,   # log(da+1) / log(100)
    "recency":               0.5,   # 1.0 if cited <6mo, linear decay to 0
    "trust_source_bonus":    0.5,   # +1 if domain in trust_sources, 0 otherwise
    "competitor_strategy":   1.0,   # +1 if matches strategy, 0 else
    "footprint_penalty":    -1.0,   # -min(1, footprint_count / FOOTPRINT_CAP)
    "price_score":           0.5,   # log(price+1) / log(2000) — neutral
    "reputation_risk":      -2.0,   # -1 if reputation_flags non-empty
}

# Footprint cap : refuse to suggest a domain a client has already accepted N
# times. Defensive default ; overridable via client.apps.
FOOTPRINT_CAP_DEFAULT = 3

# K-anonymity threshold for source 2 (cross-scan citations). Privacy guard :
# a domain only surfaces if cited in scans of ≥3 DIFFERENT clients of the
# same country+language. Protects against inter-tenant leak.
CROSS_SCAN_MIN_DISTINCT_CLIENTS = 3

# Recency : full credit for citations < 6 months old, linear to 0 at 24 months.
RECENCY_FULL_MONTHS = 6
RECENCY_ZERO_MONTHS = 24

# Authority tiers (used for diversification + badges)
TIER_1_MIN_DA = 70   # Tier-1 = "premium / national" authority
TIER_MID_MIN_DA = 40  # Mid = mainstream blogs / specialized magazines

# Diversification : compose top-K from tiers + price bands
DIVERSIFICATION_DEFAULT_TOP_K = 5


# ─── Output schema ──────────────────────────────────────────────────────


@dataclass
class Suggestion:
    """One suggested media. JSON-serialized in the endpoint response."""
    domain: str
    url: str
    score: float
    price_eur: float | None
    da: int | None
    tf: int | None
    cf: int | None
    rd: int | None
    llm_citation_count: int
    media_group: str | None
    authority_badge: str             # "tier-1" | "mid" | "niche" | "unknown"
    breakdown: dict                  # {"reasons": [...], "risks": [...]}
    sample_recent_article_url: str | None
    source: str                      # primary attribution: see CASCADE_SOURCES
    sources_seen: list[str]          # all sources that surfaced this domain
    competitor_co_cited: list[str]   # competitor brand names cited together in this scan


# ─── Internal candidate state ───────────────────────────────────────────


@dataclass
class _Candidate:
    """Mutable accumulator while scoring. Folded into a Suggestion at the end."""
    domain: str
    sources: set[str] = field(default_factory=set)
    # Signal accumulators
    same_scan_citation_count: int = 0
    same_scan_providers: set[str] = field(default_factory=set)
    cross_scan_citation_count: int = 0
    cross_scan_distinct_clients: int = 0
    sample_url: str | None = None
    in_trust_sources: bool = False
    in_catalog: bool = False
    # Catalog-sourced fields (set if we found a row, else None)
    country: str | None = None
    language: str | None = None
    price_eur: float | None = None
    da: int | None = None
    tf: int | None = None
    cf: int | None = None
    rd: int | None = None
    media_group: str | None = None
    reputation_flags: list[str] = field(default_factory=list)
    llm_citation_decayed: float = 0.0
    llm_citation_last_seen: datetime | None = None
    topic_areas: list[str] = field(default_factory=list)
    audience_tags: list[str] = field(default_factory=list)   # Haiku-classified (MR.4 #2)
    editorial_voice: str | None = None                       # Haiku-classified (MR.4 #2)
    # Phase MR.2 patch 1 — "LinkFinder confirmed not-buyable" signal.
    # When linkfinder_last_check is set AND price_eur is NULL, the
    # endpoint refused this domain in the last check window. Used by
    # _hard_filter to drop institutional/gov/no-marketplace domains
    # that would otherwise rank high (ameli.fr, vidal.fr w/o LF deal).
    linkfinder_last_check: datetime | None = None
    # Per-client signals (computed once per request)
    footprint_count: int = 0
    competitor_match: bool = False  # competitor cited on this domain in scan
    competitor_names: list[str] = field(default_factory=list)  # names of competitors co-cited


# ─── Public API ─────────────────────────────────────────────────────────


def suggest(
    db: Session,
    *,
    content_item,                       # ScanContentItem (worker or api ORM)
    strategy: str = "match_competitor", # "match_competitor" | "avoid_competitor"
    price_max: float | None = None,
    require_price: bool = False,
    exclude_domains: set[str] | None = None,
    top_k: int = DIVERSIFICATION_DEFAULT_TOP_K,
    weights: dict[str, float] | None = None,
    footprint_cap: int = FOOTPRINT_CAP_DEFAULT,
    use_llm_fallback: bool = False,     # Phase MR.3 — source 5 (credit-debited)
    openai_api_key: str | None = None,
) -> dict:
    """Return top-K replacement-media suggestions for a netlinking content item.

    Returns ``{"suggestions": [...], "llm_fallback_used": bool, "llm_new_count":
    int, "diagnostics": {...}}``. Does NOT debit credits — the API debits before
    enqueue. `llm_new_count` lets the worker decide whether to refund (0 new
    media → refund per the ratified policy). Does NOT mutate state — caller
    writes media_feedback on accept.

    Source 5 (LLM web_search) runs ONLY when `use_llm_fallback=True` AND an
    `openai_api_key` is provided. It discovers buyable media via web search,
    re-validates them through LinkFinder (price), then runs them through the
    same hard filters + scoring as the DB sources.

    Raises ``IntentNotEligibleError`` when the underlying question's
    intent_category is in SAFETY_INTENTS — Phase B compliance guard, identical
    to the opportunity scorer's existing check.
    """
    from models import Scan, ScanQuestion

    # ── Resolve scan + question + workspace context ──────────────────────
    scan = db.query(Scan).filter(Scan.id == content_item.scan_id).first()
    if not scan:
        return _empty_result("scan_not_found")

    question = _resolve_question_for_item(scan.id, content_item, db)
    if not question:
        return _empty_result("question_not_found")

    if is_safety_intent(question.intent_category):
        raise IntentNotEligibleError(
            intent_category=question.intent_category,
            message=(
                f"Question intent '{question.intent_category}' blocks "
                f"third-party brand placement (compliance / editorial fit). "
                f"Replace with an FAQ on your own site instead."
            ),
        )

    country, language = _resolve_locale(scan)
    if not country or not language:
        # Catalog only stores normalized locales — without one we can't
        # match anything reliably. Surface as empty rather than guess.
        return _empty_result("locale_unmapped")

    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    exclude_norm: set[str] = {_normalize_domain(d) for d in (exclude_domains or set())}
    exclude_norm.discard("")

    # ── Build the per-request filter context ─────────────────────────────
    own_brand_domains = _resolve_own_brand_domains(scan, db)
    competitor_domains = _resolve_competitor_domains(str(scan.id), db)
    trust_domains = _resolve_trust_domains(scan.client_id, db)

    # Phase MR.4 #2 — target audience + voice tokens (from brand + workspace
    # brief) for persona_audience + editorial_voice_match scoring. Built once.
    audience_tokens, voice_tokens = _resolve_audience_voice_context(scan, db)

    # User-rejected on THIS item (hard skip)
    rejected_for_item = _resolve_rejected_for_item(str(content_item.id), db)
    exclude_norm |= rejected_for_item

    # Footprint counts per (client, domain) — count of accepted suggestions
    footprint = _resolve_footprint(str(scan.client_id), db)

    # ── Run the cascade ──────────────────────────────────────────────────
    candidates: dict[str, _Candidate] = {}

    # Source 1 — same scan citations
    _ingest_same_scan_citations(
        db, str(scan.id), str(question.id), candidates,
        country, language,
    )
    # Source 2 — cross-scan citations (k-anonymized)
    _ingest_cross_scan_citations(
        db, scan, candidates, country, language, question,
    )
    # Source 3 — trust sources
    _ingest_trust_sources(trust_domains, candidates, country, language)
    # Source 4 — media_catalog
    _ingest_media_catalog(db, candidates, country, language)

    # Source 5 — LLM web_search fallback (credit-debited, opt-in). Tracks which
    # domains were NEWLY discovered here so the worker can refund if 0 new.
    llm_fallback_used = False
    llm_new_domains: set[str] = set()
    if use_llm_fallback and openai_api_key:
        llm_fallback_used = True
        llm_new_domains = _ingest_llm_web_search(
            db, content_item, question, candidates,
            country, language, scan,
            exclude_norm | own_brand_domains | competitor_domains,
            openai_api_key,
        )

    # ── Competitor co-citation map (built ONCE, batched — Phase MR.4) ─────
    # Replaces a per-candidate N+1 (2 queries × N) with 2 queries total.
    competitor_cocitation = _build_competitor_cocitation_map(
        db, str(scan.id), competitor_domains, str(scan.client_id),
    )

    # ── Hard filters ─────────────────────────────────────────────────────
    kept: dict[str, _Candidate] = {}
    drop_reasons: dict[str, int] = defaultdict(int)
    for domain, cand in candidates.items():
        ok, reason = _hard_filter(
            cand, own_brand_domains, competitor_domains, exclude_norm,
            footprint, footprint_cap, require_price,
        )
        if not ok:
            drop_reasons[reason] += 1
            continue
        # Stamp the footprint count for scoring
        cand.footprint_count = footprint.get(domain, 0)
        # Strategy : did this domain see a competitor in this scan? Plain dict
        # lookup now (no SQL) — the names drive the breakdown explainer
        # ("Your competitors are already cited here: Bioderma").
        names = competitor_cocitation.get(domain, [])
        cand.competitor_match = bool(names)
        cand.competitor_names = names
        cand.in_trust_sources = domain in trust_domains
        kept[domain] = cand

    # ── Score + breakdown ────────────────────────────────────────────────
    scored: list[Suggestion] = []
    for cand in kept.values():
        if price_max is not None and cand.price_eur and cand.price_eur > price_max:
            drop_reasons["price_above_max"] += 1
            continue
        sug = _score_to_suggestion(
            cand, weights, strategy, footprint_cap,
            audience_tokens, voice_tokens,
        )
        scored.append(sug)

    scored.sort(key=lambda s: s.score, reverse=True)

    # ── Strategy hard filter (Phase MR.2 fix 2026-05-21) ─────────────────
    # The bipolar scoring nudge wasn't enough to discriminate match/avoid on
    # dense scans where every top candidate has competitor co-citations.
    # Hard filter : avoid_competitor keeps ONLY candidates with empty
    # competitor_co_cited ; match_competitor keeps ONLY candidates with at
    # least one. If the filter empties the list we fall back to the full
    # ranking AND set strategy_fallback=True so the UI can display a banner.
    strategy_fallback = False
    pre_filter_count = len(scored)
    if strategy == "avoid_competitor":
        clean = [s for s in scored if not s.competitor_co_cited]
        if clean:
            scored = clean
        else:
            strategy_fallback = True
    elif strategy == "match_competitor":
        matched = [s for s in scored if s.competitor_co_cited]
        if matched:
            scored = matched
        else:
            strategy_fallback = True

    # ── Diversification top-K ────────────────────────────────────────────
    top = _diversify(scored, top_k)

    # How many of the LLM-discovered domains survived filtering into the
    # scored pool — drives the worker's refund decision (0 new → refund).
    llm_new_count = sum(1 for s in scored if s.domain in llm_new_domains)

    return {
        "suggestions": [_suggestion_to_dict(s) for s in top],
        "llm_fallback_used": llm_fallback_used,
        "llm_new_count": llm_new_count,
        "diagnostics": {
            "country": country,
            "language": language,
            "candidates_raw": len(candidates),
            "candidates_after_filter": len(kept),
            "candidates_scored": pre_filter_count,
            "candidates_after_strategy": len(scored),
            "strategy_fallback": strategy_fallback,
            "llm_discovered": len(llm_new_domains),
            "llm_new_in_results": llm_new_count,
            "drop_reasons": dict(drop_reasons),
            "strategy": strategy,
            "weights": weights,
        },
    }


class IntentNotEligibleError(Exception):
    """Raised when the question's intent_category blocks netlinking suggestion."""
    def __init__(self, *, intent_category: str, message: str):
        self.intent_category = intent_category
        super().__init__(message)


# ─── Resolution helpers ─────────────────────────────────────────────────


def _resolve_question_for_item(scan_id, content_item, db: Session):
    """Lookup ScanQuestion via (scan_id, target_question text) case-insensitive.

    Mirrors `media_picker._resolve_question_id` + `worker/main.py:enqueue_
    post_publish_measurements` patterns. Returns ORM row or None.
    """
    from sqlalchemy import func
    from models import ScanQuestion

    q_text = (content_item.target_question or "").strip().lower()
    if not q_text:
        return None
    return (
        db.query(ScanQuestion)
        .filter(
            ScanQuestion.scan_id == scan_id,
            func.lower(ScanQuestion.question) == q_text,
        )
        .first()
    )


def _resolve_locale(scan) -> tuple[str | None, str | None]:
    """Pull (country, language) from scan.config.domain_brief.country via the
    same normalization as media_catalog_io. Returns (None, None) when
    unmapped.
    """
    from services.media_catalog_io import normalize_country, country_to_language

    raw_country = ((scan.config or {}).get("domain_brief") or {}).get("country")
    country = normalize_country(raw_country)
    language = country_to_language(country)
    return country, language


def _resolve_own_brand_domains(scan, db: Session) -> set[str]:
    """Union of (a) BrandResolver.promote_brands AND (b) ALL client_brands.domain
    for this client.

    Phase MR.2 patch 2 — extending beyond the per-scan promotion list catches
    sibling brands the user owns but didn't enable for this scan (e.g.
    Pierre Fabre's `aveneusa.com` shouldn't surface when scanning
    `eau-thermale-avene.fr`). The promotion list is per-scan tactical; the
    full client_brands list is the strategic "do not suggest" denylist.
    """
    out: set[str] = set()
    try:
        from services.brand_resolver import PromotionUnsetError, resolve_promotion
        try:
            promotion = resolve_promotion(scan, db)
            for b in promotion.promote_brands:
                nd = _normalize_domain(b.domain) if b.domain else ""
                if nd:
                    out.add(nd)
        except PromotionUnsetError:
            pass
    except Exception:
        logger.exception("media_replacement: resolve_promotion crashed")

    # Extend with every domain the client has ever registered as a brand.
    try:
        rows = db.execute(text("""
            SELECT DISTINCT lower(domain) FROM client_brands
             WHERE client_id = :cid AND domain IS NOT NULL AND domain <> ''
        """), {"cid": str(scan.client_id)}).fetchall()
        for (d,) in rows:
            nd = _normalize_domain(d)
            if nd:
                out.add(nd)
    except Exception:
        logger.exception("media_replacement: client_brands fetch crashed")
    return out


def _resolve_competitor_domains(scan_id: str, db: Session) -> set[str]:
    try:
        from services.competitor_domains import get_competitor_domains_for_scan
        return get_competitor_domains_for_scan(scan_id, db)
    except Exception:
        logger.exception("media_replacement: get_competitor_domains_for_scan crashed")
        return set()


def _resolve_trust_domains(client_id, db: Session) -> set[str]:
    try:
        from services.trust_sources import get_trust_sources_for_client
        return {_normalize_domain(d) for d in get_trust_sources_for_client(client_id, db)}
    except Exception:
        logger.exception("media_replacement: get_trust_sources_for_client crashed")
        return set()


def _resolve_rejected_for_item(content_item_id: str, db: Session) -> set[str]:
    """All domains the user has rejected on THIS item via /accept-suggestion."""
    rows = db.execute(text("""
        SELECT lower(domain) FROM media_feedback
         WHERE content_item_id = :iid AND action = 'rejected'
    """), {"iid": content_item_id}).fetchall()
    return {r[0] for r in rows if r[0]}


def _resolve_footprint(client_id: str, db: Session) -> dict[str, int]:
    """{domain: count_of_accepted} for this client across all items."""
    rows = db.execute(text("""
        SELECT lower(domain), COUNT(*) FROM media_feedback
         WHERE client_id = :cid AND action = 'accepted'
         GROUP BY lower(domain)
    """), {"cid": client_id}).fetchall()
    return {d: int(c) for d, c in rows if d}


# ─── Cascade sources ────────────────────────────────────────────────────


def _ingest_same_scan_citations(
    db: Session, scan_id: str, question_id: str,
    candidates: dict[str, _Candidate],
    country: str, language: str,
) -> None:
    """Source 1 — citations of the SAME scan, SAME question. Strongest signal."""
    rows = db.execute(text("""
        SELECT
            lower(c->>'domaine')                          AS domain,
            lower(slr.provider)                           AS provider,
            COALESCE(c->>'url', '')                       AS url,
            COALESCE((c->>'est_site_cible')::bool, false) AS is_target,
            COALESCE((c->>'is_pr_source')::bool, false)   AS is_pr
          FROM scan_llm_results slr,
               jsonb_array_elements(slr.citations) c
         WHERE slr.scan_id = :sid
           AND slr.question_id = :qid
           AND jsonb_typeof(slr.citations) = 'array'
    """), {"sid": scan_id, "qid": question_id}).fetchall()

    # N-runs (T1) : the rows are all for ONE question - dedupe per
    # (provider, url) so N runs citing the same page count once.
    _seen: set = set()
    for r in rows:
        if r.is_target or r.is_pr:
            continue
        d = _normalize_domain(r.domain)
        if not d:
            continue
        _k = (r.provider or "", r.url or d)
        if _k in _seen:
            continue
        _seen.add(_k)
        cand = candidates.get(d)
        if cand is None:
            cand = _Candidate(domain=d, country=country, language=language)
            candidates[d] = cand
        cand.sources.add("scan_citation")
        cand.same_scan_citation_count += 1
        if r.provider:
            cand.same_scan_providers.add(r.provider)
        if not cand.sample_url and r.url:
            cand.sample_url = r.url


def _ingest_cross_scan_citations(
    db: Session, scan, candidates: dict[str, _Candidate],
    country: str, language: str, question,
) -> None:
    """Source 2 — cross-scan citations, k-anonymized.

    A domain qualifies if cited in scans of ≥ CROSS_SCAN_MIN_DISTINCT_CLIENTS
    OTHER clients (excluding the current scan's client) with matching
    country+language. Privacy guard.
    """
    # K-anonymity via HAVING clause. We compare normalized country codes via
    # the same _COUNTRY_NORMALIZE table inside Python (the SQL stays simple
    # and the bucket loop below applies the k-anon threshold).
    rows = db.execute(text("""
        SELECT
            lower(c->>'domaine')                          AS domain,
            s.client_id                                   AS client_id,
            lower(slr.provider)                           AS provider,
            COALESCE(c->>'url', '')                       AS url,
            s.config->'domain_brief'->>'country'          AS raw_country,
            COALESCE((c->>'est_site_cible')::bool, false) AS is_target,
            COALESCE((c->>'is_pr_source')::bool, false)   AS is_pr,
            COALESCE(slr.question_id, slr.id)             AS qkey
          FROM scan_llm_results slr
          JOIN scans s ON s.id = slr.scan_id,
               jsonb_array_elements(slr.citations) c
         WHERE jsonb_typeof(slr.citations) = 'array'
           AND s.client_id <> :own_client
           AND s.id <> :own_scan
           AND slr.created_at > NOW() - INTERVAL '24 months'
    """), {"own_client": scan.client_id, "own_scan": scan.id}).fetchall()

    from services.media_catalog_io import normalize_country

    bucket: dict[str, dict] = {}
    # N-runs (T1) : one signal per (client, question, provider, domain) -
    # the k-anonymity threshold (DISTINCT clients) was already immune, this
    # keeps the count score honest at N>1.
    _seen: set = set()
    for r in rows:
        if r.is_target or r.is_pr:
            continue
        d = _normalize_domain(r.domain)
        if not d:
            continue
        rc = normalize_country(r.raw_country)
        if rc != country:
            continue
        _k = (str(r.client_id), str(r.qkey), r.provider or "", d)
        if _k in _seen:
            continue
        _seen.add(_k)
        b = bucket.setdefault(d, {"clients": set(), "count": 0, "providers": set(), "sample_url": None})
        b["clients"].add(str(r.client_id))
        b["count"] += 1
        if r.provider:
            b["providers"].add(r.provider)
        if not b["sample_url"] and r.url:
            b["sample_url"] = r.url

    for d, b in bucket.items():
        if len(b["clients"]) < CROSS_SCAN_MIN_DISTINCT_CLIENTS:
            continue
        cand = candidates.get(d)
        if cand is None:
            cand = _Candidate(domain=d, country=country, language=language)
            candidates[d] = cand
        cand.sources.add("cross_scan")
        cand.cross_scan_citation_count = b["count"]
        cand.cross_scan_distinct_clients = len(b["clients"])
        if not cand.sample_url:
            cand.sample_url = b["sample_url"]


def _ingest_trust_sources(
    trust_domains: set[str],
    candidates: dict[str, _Candidate],
    country: str, language: str,
) -> None:
    """Source 3 — client's workspace-declared trust sources for this vertical."""
    for d in trust_domains:
        cand = candidates.get(d)
        if cand is None:
            cand = _Candidate(domain=d, country=country, language=language)
            candidates[d] = cand
        cand.sources.add("trust_sources")
        cand.in_trust_sources = True


def _ingest_media_catalog(
    db: Session, candidates: dict[str, _Candidate],
    country: str, language: str,
) -> None:
    """Source 4 — media_catalog filtered by country+language.

    We don't filter by vertical here — the catalog stores raw industry
    strings as vertical[] elements, fuzzy matching at filter time is too
    expensive AND would exclude many legitimate candidates. Vertical
    relevance is already encoded via topic_areas overlap (Sprint 3 will
    add Jaccard scoring on these).
    """
    rows = db.execute(text("""
        SELECT
            domain, price_eur, da, tf, cf, rd, media_group,
            reputation_flags, llm_citation_count, llm_citation_decayed,
            llm_citation_last_seen, topic_areas, linkfinder_last_check,
            audience_tags, editorial_voice
          FROM media_catalog
         WHERE country = :c AND language = :l
    """), {"c": country, "l": language}).fetchall()

    for r in rows:
        d = _normalize_domain(r.domain)
        if not d:
            continue
        cand = candidates.get(d)
        if cand is None:
            cand = _Candidate(domain=d, country=country, language=language)
            candidates[d] = cand
        cand.sources.add("media_catalog")
        cand.in_catalog = True
        cand.price_eur = float(r.price_eur) if r.price_eur is not None else None
        cand.da = int(r.da) if r.da is not None else None
        cand.tf = int(r.tf) if r.tf is not None else None
        cand.cf = int(r.cf) if r.cf is not None else None
        cand.rd = int(r.rd) if r.rd is not None else None
        cand.media_group = r.media_group
        cand.reputation_flags = list(r.reputation_flags or [])
        cand.llm_citation_decayed = float(r.llm_citation_decayed or 0)
        cand.llm_citation_last_seen = r.llm_citation_last_seen
        cand.topic_areas = list(r.topic_areas or [])
        cand.linkfinder_last_check = r.linkfinder_last_check
        cand.audience_tags = list(r.audience_tags or [])
        cand.editorial_voice = r.editorial_voice


def _ingest_llm_web_search(
    db: Session, content_item, question,
    candidates: dict[str, _Candidate],
    country: str, language: str, scan,
    exclude_domains: set[str],
    openai_api_key: str,
) -> set[str]:
    """Source 5 — LLM web_search → buyable media → LinkFinder re-validation.

    Returns the set of domains NEWLY added by this source (not already present
    from sources 1-4). The caller counts how many survive to the scored pool
    for the refund decision.

    Each discovered domain is LinkFinder-priced inline (1 batch call) so it can
    be scored on the same price/authority axis as catalog candidates. Domains
    LinkFinder can't price keep price_eur=None (still surfaced — cas A outreach).
    """
    topic = " · ".join([t for t in [content_item.topic_name, content_item.target_question] if t])
    persona = content_item.persona_name or ""
    vertical = ((scan.config or {}).get("domain_brief") or {}).get("industry") or ""

    try:
        from services.media_web_discovery import discover_media_via_web
        discovered = discover_media_via_web(
            topic=topic, persona=persona, country=country, language=language,
            vertical=vertical, exclude_domains=exclude_domains,
            openai_api_key=openai_api_key,
        )
    except Exception:
        logger.exception("media_replacement: LLM web discovery crashed")
        return set()

    if not discovered:
        return set()

    # LinkFinder price re-validation for the discovered domains (1 batch call).
    prices: dict[str, dict] = {}
    try:
        from seo_llm.src.link_finder_client import LinkFinderClient
        lf = LinkFinderClient()
        if lf.is_api_configured:
            prices = lf.get_prices_batch([m["domain"] for m in discovered])
    except Exception:
        logger.exception("media_replacement: LinkFinder re-validation of LLM media crashed")

    new_domains: set[str] = set()
    for m in discovered:
        d = _normalize_domain(m["domain"])
        if not d:
            continue
        is_new = d not in candidates
        cand = candidates.get(d)
        if cand is None:
            cand = _Candidate(domain=d, country=country, language=language)
            candidates[d] = cand
        cand.sources.add("llm_web_search")
        # Reason text carried via sample (the "why it fits" from the LLM)
        if m.get("reason") and not cand.sample_url:
            # No URL from web discovery ; leave sample_url None, reason is logged
            pass
        info = prices.get(d) or {}
        if info.get("source") != "not_found":
            if info.get("prix_ht") is not None and cand.price_eur is None:
                cand.price_eur = info.get("prix_ht")
        if is_new:
            new_domains.add(d)
    return new_domains


# ─── Filters ────────────────────────────────────────────────────────────


def _hard_filter(
    cand: _Candidate,
    own_brand_domains: set[str],
    competitor_domains: set[str],
    exclude_norm: set[str],
    footprint: dict[str, int],
    footprint_cap: int,
    require_price: bool,
) -> tuple[bool, str]:
    """Apply the hard filter stack. Returns (kept, drop_reason)."""
    d = cand.domain
    if d in exclude_norm:
        return False, "user_rejected"
    for own in own_brand_domains:
        if own and (d == own or d.endswith("." + own)):
            return False, "own_brand"
    for comp in competitor_domains:
        if comp and (d == comp or d.endswith("." + comp)):
            return False, "competitor"
    # Universal authority TLDs handled via trust_sources.is_universal_authority_tld
    try:
        from services.trust_sources import is_universal_authority_tld
        if is_universal_authority_tld(d):
            return False, "gov_authority"
    except Exception:
        pass
    # E-commerce / social / blog patterns. is_excluded_url returns
    # (bool, reason) — unpack carefully ; a non-empty tuple is always truthy.
    try:
        from services.url_filter import is_excluded_url
        excluded, why = is_excluded_url(f"https://{d}/")
        if excluded:
            return False, f"universal:{why}"
    except Exception:
        pass
    # Footprint cap
    if footprint.get(d, 0) >= footprint_cap:
        return False, "footprint_cap"
    # Phase MR.2 patch 1 — "LinkFinder confirmed not-buyable".
    # When the catalog row was checked by LinkFinder AND came back with
    # no price, the domain doesn't sell paid placement. Strict drop,
    # NOT a score penalty, since the signal is decisive (we asked,
    # they said no). Domains never-checked (NULL) pass through —
    # we don't know either way and outreach may work.
    if cand.linkfinder_last_check is not None and cand.price_eur is None:
        return False, "not_buyable"
    # Price gate (cas B — user wants only buyable)
    if require_price and (cand.price_eur is None or cand.price_eur <= 0):
        return False, "no_price"
    return True, ""


def _build_competitor_cocitation_map(
    db: Session, scan_id: str,
    competitor_domains: set[str], client_id: str,
) -> dict[str, list[str]]:
    """Phase MR.4 — batch replacement for the per-domain N+1.

    Builds, in 2 queries for the WHOLE scan, a map
    ``{cited_media_domain: [competitor_brand_name, ...]}`` of which competitor
    brands were cited in the SAME LLM responses as each media domain.

    Was: 2 SQL queries × N surviving candidates (~255s on dense scans).
    Now: 1 query to expand citations × responses-with-competitors + 1 query to
    translate competitor domains → brand names. The per-candidate lookup is a
    plain dict access. Only domains WITH a competitor co-citation appear in the
    map; absence = (False, []).
    """
    if not competitor_domains:
        return {}

    # Query 1 — every (cited_domain, competitor_domains_dict) pair for responses
    # that have at least one competitor. One row per citation per qualifying
    # response ; we fold them into a per-domain set of competitor domains.
    rows = db.execute(text("""
        SELECT
            lower(c->>'domaine')        AS domain,
            slr.competitor_domains      AS comp
          FROM scan_llm_results slr,
               jsonb_array_elements(slr.citations) c
         WHERE slr.scan_id = :sid
           AND jsonb_typeof(slr.citations) = 'array'
           AND slr.competitor_domains IS NOT NULL
           AND slr.competitor_domains <> '{}'::jsonb
    """), {"sid": scan_id}).fetchall()

    domain_to_comp_domains: dict[str, set[str]] = defaultdict(set)
    all_comp_domains: set[str] = set()
    for raw_domain, comp in rows:
        d = _normalize_domain(raw_domain)
        if not d or not isinstance(comp, dict):
            continue
        for k in comp.keys():
            cd = _normalize_domain(k)
            if cd and cd in competitor_domains:
                domain_to_comp_domains[d].add(cd)
                all_comp_domains.add(cd)

    if not domain_to_comp_domains:
        return {}

    # Query 2 — translate the competitor domains we actually saw → brand names.
    name_by_domain: dict[str, str] = {}
    if all_comp_domains:
        name_rows = db.execute(text("""
            SELECT DISTINCT lower(domain) AS d, name
              FROM client_brands
             WHERE client_id = :cid
               AND lower(domain) = ANY(:doms)
        """), {"cid": client_id, "doms": list(all_comp_domains)}).fetchall()
        for d, n in name_rows:
            if d and n:
                name_by_domain[d] = n.strip()

    # Fold to {media_domain: sorted([brand names | bare domain fallback])}
    out: dict[str, list[str]] = {}
    for media_domain, comp_set in domain_to_comp_domains.items():
        names = {name_by_domain.get(cd, cd) for cd in comp_set}
        out[media_domain] = sorted(n for n in names if n)
    return out


# ─── Scoring + explainability ───────────────────────────────────────────


def _score_to_suggestion(
    cand: _Candidate,
    weights: dict[str, float],
    strategy: str,
    footprint_cap: int,
    audience_tokens: set[str] | None = None,
    voice_tokens: set[str] | None = None,
) -> Suggestion:
    """Compute the weighted score per component, build human-readable breakdown."""
    reasons: list[str] = []
    risks: list[str] = []
    score = 0.0
    audience_tokens = audience_tokens or set()
    voice_tokens = voice_tokens or set()

    # llm_citation_topic — combine same-scan (strongest) and decayed catalog count
    same = cand.same_scan_citation_count
    decayed = cand.llm_citation_decayed
    # Soft normalization : 10+ citations on topic = saturated
    topic_signal = min(1.0, (same * 1.0 + decayed * 0.3) / 10.0)
    contrib = weights["llm_citation_topic"] * topic_signal
    score += contrib
    if same > 0:
        providers_n = len(cand.same_scan_providers)
        reasons.append(
            f"Cited by AIs on this exact question"
            + (f" ({providers_n} different AI{'s' if providers_n > 1 else ''})"
               if providers_n else "")
        )
    elif decayed >= 3:
        reasons.append("Often cited by AIs on related topics")
    elif "llm_web_search" in cand.sources and "scan_citation" not in cand.sources:
        # Source-5-only candidate : no citation history, found via live web search.
        reasons.append("Found by AI web search — relevant outlet for this topic")

    # persona_audience (Phase MR.4 #2) — overlap between this media's
    # Haiku-classified audience_tags and the brand/persona target-audience
    # tokens. Overlap-coefficient (intersection / min set size) so a media
    # with few but matching tags isn't penalized vs one with many tags.
    if weights.get("persona_audience") and audience_tokens and cand.audience_tags:
        media_aud = _tokenize(" ".join(cand.audience_tags))
        sig = _overlap_coefficient(media_aud, audience_tokens)
        if sig > 0:
            score += weights["persona_audience"] * sig
            if sig >= 0.34:
                reasons.append("Audience matches your target reader")

    # editorial_voice_match (Phase MR.4 #2) — token overlap between this
    # media's voice descriptor and the brand's editorial voice.
    if weights.get("editorial_voice_match") and voice_tokens and cand.editorial_voice:
        media_voice = _tokenize(cand.editorial_voice)
        sig = _overlap_coefficient(media_voice, voice_tokens)
        if sig > 0:
            score += weights["editorial_voice_match"] * sig
            if sig >= 0.34:
                reasons.append("Editorial tone fits your brand voice")

    # authority — log scale on DA
    if cand.da is not None and cand.da > 0:
        auth_signal = math.log(cand.da + 1) / math.log(101)
        contrib = weights["authority"] * auth_signal
        score += contrib
        reasons.append(f"Well-established, trusted site (authority {cand.da}/100)")
    elif cand.da is None:
        # Sparse data — not a risk, just diminished signal
        pass

    # recency
    if cand.llm_citation_last_seen:
        delta = datetime.utcnow() - cand.llm_citation_last_seen
        months = max(0.0, delta.total_seconds() / (30.44 * 86400))
        if months < RECENCY_FULL_MONTHS:
            recency_signal = 1.0
        elif months > RECENCY_ZERO_MONTHS:
            recency_signal = 0.0
        else:
            recency_signal = 1.0 - (months - RECENCY_FULL_MONTHS) / (RECENCY_ZERO_MONTHS - RECENCY_FULL_MONTHS)
        contrib = weights["recency"] * recency_signal
        score += contrib
        if months < RECENCY_FULL_MONTHS:
            reasons.append("Cited recently (under 6 months)")

    # trust_source_bonus
    if cand.in_trust_sources:
        contrib = weights["trust_source_bonus"]
        score += contrib
        reasons.append("Présent dans tes sources de confiance déclarées")

    # competitor_strategy — BIPOLAR : reward aligned cases, penalize misaligned
    # ones. Otherwise candidates that all have competitor_match=True (frequent
    # on dense scans) get the SAME contrib regardless of toggle = toggle has no
    # discriminating effect. Bipolar makes match/avoid actually re-rank.
    w_cs = weights.get("competitor_strategy", 0)
    if w_cs:
        names = cand.competitor_names or []
        names_str = ", ".join(names[:3]) if names else "le concurrent"
        if strategy == "match_competitor":
            if cand.competitor_match:
                score += w_cs
                reasons.append(f"Your competitors are already cited here: {names_str}")
            else:
                score += -w_cs * 0.5  # mild penalty : we wanted match, didn't find
                risks.append("None of your competitors are cited here")
        elif strategy == "avoid_competitor":
            if cand.competitor_match:
                score += -w_cs
                risks.append(f"Your competitors already appear here: {names_str}")
            else:
                score += w_cs * 0.5  # mild bonus : clean differentiation
                reasons.append("No competitor here — clear ground to stand out")

    # footprint_penalty
    if cand.footprint_count > 0:
        penalty_signal = min(1.0, cand.footprint_count / max(1, footprint_cap))
        contrib = weights["footprint_penalty"] * penalty_signal  # weight is negative
        score += contrib
        risks.append(
            f"Tu as déjà publié {cand.footprint_count}× sur ce média "
            f"(plafond {footprint_cap})"
        )

    # price_score — modest positive signal for having a buyable price
    if cand.price_eur and cand.price_eur > 0:
        price_signal = min(1.0, math.log(cand.price_eur + 1) / math.log(2001))
        contrib = weights["price_score"] * price_signal
        score += contrib

    # reputation_risk
    if cand.reputation_flags:
        contrib = weights["reputation_risk"]
        score += contrib
        risks.append(f"Drapeaux qualité : {', '.join(cand.reputation_flags)}")

    # Build authority badge for UI
    badge = _authority_badge(cand.da)

    # If price is null but the suggestion still surfaces, note it (cas A)
    if cand.price_eur is None and weights.get("price_score"):
        risks.append("No known price — you'll need to contact the media directly")

    return Suggestion(
        domain=cand.domain,
        url=f"https://www.{cand.domain}/",
        score=round(score, 3),
        price_eur=cand.price_eur,
        da=cand.da, tf=cand.tf, cf=cand.cf, rd=cand.rd,
        llm_citation_count=cand.same_scan_citation_count + cand.cross_scan_citation_count,
        media_group=cand.media_group,
        authority_badge=badge,
        breakdown={"reasons": reasons, "risks": risks},
        sample_recent_article_url=cand.sample_url,
        source=_primary_source(cand),
        sources_seen=sorted(cand.sources),
        competitor_co_cited=cand.competitor_names,
    )


def _authority_badge(da: int | None) -> str:
    if da is None:
        return "unknown"
    if da >= TIER_1_MIN_DA:
        return "tier-1"
    if da >= TIER_MID_MIN_DA:
        return "mid"
    return "niche"


def _primary_source(cand: _Candidate) -> str:
    """Primary attribution for the UI badge ('source' field). Highest-signal wins."""
    if "scan_citation" in cand.sources:
        return "scan_citation"
    if "cross_scan" in cand.sources:
        return "cross_scan"
    if "trust_sources" in cand.sources:
        return "trust_sources"
    if "media_catalog" in cand.sources:
        return "media_catalog"
    return "unknown"


# ─── Diversification ────────────────────────────────────────────────────


def _diversify(scored: list[Suggestion], top_k: int) -> list[Suggestion]:
    """Compose top_k from {1 tier-1, 2-3 mid, 1 niche} when available.

    Fallback : if a tier is empty we backfill from the next-richest tier
    by raw score. Also caps at 2 per media_group (currently mostly None,
    so the cap is mostly inactive).
    """
    if top_k <= 0 or not scored:
        return []

    tiers = {"tier-1": [], "mid": [], "niche": [], "unknown": []}
    for s in scored:
        tiers.setdefault(s.authority_badge, tiers["unknown"]).append(s)

    # Target distribution by tier
    targets = {"tier-1": 1, "mid": 3, "niche": 1, "unknown": top_k}
    out: list[Suggestion] = []
    group_counts: dict[str, int] = defaultdict(int)

    def _try_add(s: Suggestion) -> bool:
        if s in out:
            return False
        if s.media_group and group_counts[s.media_group] >= 2:
            return False
        out.append(s)
        if s.media_group:
            group_counts[s.media_group] += 1
        return True

    for tier in ("tier-1", "mid", "niche"):
        target = targets[tier]
        for s in tiers[tier]:
            if len(out) >= top_k:
                break
            if sum(1 for x in out if x.authority_badge == tier) >= target:
                break
            _try_add(s)

    # Backfill : any remaining slot pulled from the global top by score
    if len(out) < top_k:
        for s in scored:
            if len(out) >= top_k:
                break
            _try_add(s)

    return out[:top_k]


# ─── Utilities ──────────────────────────────────────────────────────────

# Stopwords dropped from audience / voice token sets so overlap reflects
# meaningful terms, not glue words. Small bilingual (FR/EN) set — the inputs
# are short brief snippets, not prose.
_TOKEN_STOPWORDS: frozenset[str] = frozenset({
    "the", "and", "for", "with", "you", "your", "who", "are", "our", "des",
    "les", "une", "des", "pour", "avec", "qui", "que", "aux", "leur", "leurs",
    "from", "this", "that", "their", "them", "ages", "age", "based",
})


def _tokenize(text_in: str | None) -> set[str]:
    """Lowercase word set, len>=3, stopwords dropped. For lexical overlap."""
    if not text_in:
        return set()
    words = re.findall(r"[a-zA-Zà-ÿ]+", str(text_in).lower())
    return {w for w in words if len(w) >= 3 and w not in _TOKEN_STOPWORDS}


def _overlap_coefficient(a: set[str], b: set[str]) -> float:
    """|a ∩ b| / min(|a|, |b|). 0..1. Robust to size mismatch (a few good tags
    vs a long brief shouldn't dilute the signal like Jaccard would)."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / min(len(a), len(b))


def _resolve_audience_voice_context(scan, db: Session) -> tuple[set[str], set[str]]:
    """Build (audience_tokens, voice_tokens) from the workspace brief +
    focus-brand brief. Used to score persona_audience + editorial_voice_match.

    Sources, merged :
      - client.apps['client_brief'] : target_audience, editorial_voice (workspace)
      - client_brands.brief (focus brand) : target_audience, audience_segments,
        editorial_voice, tonality, tone_dos
    Returns empty sets when no brief exists (scoring components stay dark).
    """
    audience_parts: list[str] = []
    voice_parts: list[str] = []

    try:
        from models import Client, ClientBrand
        client = db.query(Client).filter(Client.id == scan.client_id).first()
        workspace = ((client.apps if client else None) or {}).get("client_brief") or {}
        if workspace.get("target_audience"):
            audience_parts.append(str(workspace["target_audience"]))
        if workspace.get("editorial_voice"):
            voice_parts.append(str(workspace["editorial_voice"]))

        # Focus brand brief (per-brand overrides). scan.focus_brand_id, fallback
        # to the first promotion brand.
        brand_id = getattr(scan, "focus_brand_id", None)
        if not brand_id:
            pbids = getattr(scan, "promotion_brand_ids", None) or []
            brand_id = pbids[0] if pbids else None
        if brand_id:
            brand = db.query(ClientBrand).filter(ClientBrand.id == brand_id).first()
            brief = (brand.brief if brand else None) or {}
            if brief.get("target_audience"):
                audience_parts.append(str(brief["target_audience"]))
            for seg in (brief.get("audience_segments") or []):
                audience_parts.append(str(seg))
            if brief.get("editorial_voice"):
                voice_parts.append(str(brief["editorial_voice"]))
            for t in (brief.get("tonality") or []):
                voice_parts.append(str(t))
            for t in (brief.get("tone_dos") or []):
                voice_parts.append(str(t))
    except Exception:
        logger.exception("media_replacement: audience/voice context resolution crashed")

    return _tokenize(" ".join(audience_parts)), _tokenize(" ".join(voice_parts))


def _normalize_domain(raw: str | None) -> str:
    if not raw:
        return ""
    s = str(raw).strip().lower()
    s = re.sub(r"^https?://", "", s)
    if s.startswith("www."):
        s = s[4:]
    s = s.split("/", 1)[0].rstrip(".")
    return s if "." in s else ""


def _empty_result(reason: str) -> dict:
    return {
        "suggestions": [],
        "llm_fallback_used": False,
        "llm_new_count": 0,
        "diagnostics": {"empty_reason": reason},
    }


def _suggestion_to_dict(s: Suggestion) -> dict:
    return {
        "domain": s.domain,
        "url": s.url,
        "score": s.score,
        "price_eur": s.price_eur,
        "da": s.da, "tf": s.tf, "cf": s.cf, "rd": s.rd,
        "llm_citation_count": s.llm_citation_count,
        "media_group": s.media_group,
        "authority_badge": s.authority_badge,
        "breakdown": s.breakdown,
        "sample_recent_article_url": s.sample_recent_article_url,
        "source": s.source,
        "sources_seen": s.sources_seen,
        "competitor_co_cited": s.competitor_co_cited,
    }
