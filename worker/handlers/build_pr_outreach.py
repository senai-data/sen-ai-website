"""Handler : Sprint 9 PR / journalist outreach list (feature #7).

For each scan we mine `scan_llm_results.citations[]` to find the press
and blog domains that LLMs already cite when answering buyer questions
for this brand or its competitors, then aggregate per-domain so the user
gets a shortlist of "journalists / publications already covering my space
but not me" - the obvious outreach targets.

Sources :
  - scan_llm_results.citations[]            : the URLs LLMs returned
  - scan_llm_results.brand_mentions[]       : who was named in each response
  - scan_brand_classifications              : which brands are my_brand
                                              vs competitor for this scan
  - domain_classifications                  : Gemini site_type label
                                              (News / Blog / Brand / E-com /
                                              Forum / Encyclopedia / ...)
  - media_catalog                           : Babbar DA + price + vertical
                                              (looked up by domain only)

Filtering : we drop domains that are
  - the focus brand domain                 (you, not a journalist)
  - any competitor's brand domain          (theirs, not a journalist)
  - 'Brand' / 'E-commerce' per classification (other corporate sites)
  - 'Forum' (Sprint 8 territory : Reddit / Doctissimo)
  - 'Encyclopedia' (Sprint 4 territory : Wikipedia)
  - explicit platform hosts (reddit/wikipedia/youtube/google docs etc.)

Per-domain output :
  - classification : competitor_only / shared / target_only
  - leverage_score : 0-100 composite
  - top_pages JSONB up to 5 : url, contexte, citation_count, winning_questions

Cost : zero LLM. Pure SQL aggregation + DB lookups.
"""
from __future__ import annotations

import logging
import math
from urllib.parse import urlparse

from sqlalchemy import text as _text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MAX_DOMAINS_PER_RUN = 200
MAX_PAGES_PER_DOMAIN = 5
MAX_WINNING_QUESTIONS_PER_DOMAIN = 20

# Hard-coded platform hosts that are categorically not journalist outreach
# targets. The classification heuristic above also catches most of these
# (Forum / Encyclopedia / E-commerce) but listing them explicitly means we
# don't depend on Gemini having classified the domain yet.
PLATFORM_HOSTS = {
    "reddit.com", "old.reddit.com", "np.reddit.com", "i.reddit.com",
    "wikipedia.org", "wikidata.org", "wiktionary.org",
    "youtube.com", "youtu.be", "m.youtube.com",
    "facebook.com", "m.facebook.com", "instagram.com", "x.com", "twitter.com",
    "linkedin.com", "pinterest.com", "tiktok.com",
    "amazon.fr", "amazon.com", "amazon.de", "amazon.co.uk", "amazon.es",
    "google.com", "docs.google.com", "drive.google.com", "translate.google.com",
    "scholar.google.com", "maps.google.com",
    "bing.com", "duckduckgo.com", "yahoo.com",
    "github.com", "gitlab.com",
    "vertexaisearch.cloud.google.com",  # Gemini grounding redirect host
}

EXCLUDED_SITE_TYPES = {"Brand", "E-commerce", "Forum", "Encyclopedia"}


def _norm_domain(raw: str | None) -> str:
    """Lowercase + strip 'www.' + strip trailing slash. Empty string on
    None or unparseable input. Mirror of the catalog's normalisation
    rules so domain joins line up with media_catalog rows."""
    if not raw:
        return ""
    d = raw.strip().lower()
    if d.startswith("www."):
        d = d[4:]
    return d.rstrip("/").strip()


def _is_platform_host(domain: str) -> bool:
    """True if the domain (or one of its parent suffixes) is in the hard
    platform blocklist. Catches 'old.reddit.com' as reddit.com etc."""
    if not domain:
        return False
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        suffix = ".".join(parts[i:])
        if suffix in PLATFORM_HOSTS:
            return True
    return domain in PLATFORM_HOSTS


def _scan_brand_domains(
    db: Session, scan_id: str
) -> tuple[str, set[str], dict[str, str], set[str]]:
    """Return (target_domain, competitor_domains, brand_name_by_lower,
    competitor_names_lower).

    target_domain     : focus brand's domain (lowercased, www-stripped). May
                        be empty when the scan has no domain configured.
    competitor_domains: lowercased domains of every competitor brand
                        classified on this scan. Used to drop "competitor
                        homepage" rows from the outreach list.
    brand_name_by_lower: map of lowercased brand_name → canonical display
                        name. Used to resolve brand_mentions[].brand_name
                        back to a canonical label.
    competitor_names_lower : set of lowercased names + aliases for brands
                        classified as 'competitor'. **The handler MUST
                        match brand_mentions against this set, not against
                        the raw name_map** - otherwise the noise floor
                        (ingredients, drug names, publication names,
                        unrelated mentions the brand analyzer flagged)
                        flood the per-domain competitor_brands array.
                        Bug from initial S9 ship 2026-05-28 : an ameli.fr
                        row showed 184 'competitors' when only 38 were
                        actually classified as such.
    """
    rows = db.execute(_text(
        """
        SELECT cb.id, cb.name, cb.canonical_name, cb.domain,
               cb.aliases, sbc.classification
          FROM scan_brand_classifications sbc
          JOIN client_brands cb ON cb.id = sbc.brand_id
         WHERE sbc.scan_id = :scan_id
           AND sbc.classification IN ('my_brand', 'competitor')
        """
    ), {"scan_id": scan_id}).fetchall()

    target_domain = ""
    competitor_domains: set[str] = set()
    name_map: dict[str, str] = {}
    competitor_names_lower: set[str] = set()
    for r in rows:
        domain = _norm_domain(r.domain)
        canonical_label = (r.canonical_name or r.name or "").strip()
        names = [r.name, r.canonical_name, *(r.aliases or [])]
        for n in names:
            if not n:
                continue
            lower = n.strip().lower()
            name_map[lower] = canonical_label or n.strip()
            if r.classification == "competitor":
                competitor_names_lower.add(lower)
        if r.classification == "my_brand" and domain and not target_domain:
            target_domain = domain
        elif r.classification == "competitor" and domain:
            competitor_domains.add(domain)
    return target_domain, competitor_domains, name_map, competitor_names_lower


def _cited_pages_with_mentions(db: Session, scan_id: str) -> list[dict]:
    """One row per (slr, citation). Each row carries the URL/domain/contexte
    of the citation plus the set of brand names mentioned in that LLM
    response (so the caller can decide if the response was about the
    target, a competitor, or both)."""
    sql = _text(
        """
        SELECT slr.id::text       AS slr_id,
               slr.question_id::text AS question_id,
               sq.question           AS question,
               slr.provider          AS provider,
               citation->>'url'       AS raw_url,
               lower(citation->>'domaine') AS raw_domain,
               citation->>'contexte'  AS contexte,
               citation->>'titre'     AS title,
               slr.brand_mentions     AS brand_mentions
          FROM scan_llm_results slr
          JOIN LATERAL jsonb_array_elements(slr.citations) AS citation ON true
          LEFT JOIN scan_questions sq ON sq.id = slr.question_id
         WHERE slr.scan_id = :scan_id
           AND citation->>'url' IS NOT NULL
        """
    )
    return [
        {
            "slr_id": r.slr_id,
            "question_id": r.question_id,
            "question": r.question,
            "provider": r.provider,
            "url": r.raw_url,
            "domain": _norm_domain(r.raw_domain or (urlparse(r.raw_url).netloc if r.raw_url else "")),
            "contexte": (r.contexte or "")[:300],
            "title": (r.title or "")[:200] if r.title else None,
            "brand_mentions": r.brand_mentions or [],
        }
        for r in db.execute(sql, {"scan_id": scan_id}).fetchall()
    ]


def _domain_classifications(db: Session, domains: set[str]) -> dict[str, str]:
    """Bulk-fetch site_type for the given domains from
    domain_classifications. Domains not in the table → not in the result
    (caller must handle the absence as 'unclassified')."""
    if not domains:
        return {}
    rows = db.execute(
        _text("SELECT domain, site_type FROM domain_classifications WHERE domain = ANY(:d)"),
        {"d": sorted(list(domains))},
    ).fetchall()
    return {r.domain: r.site_type for r in rows}


def _media_catalog_lookup(db: Session, domains: set[str]) -> dict[str, dict]:
    """Bulk-fetch authority + price signals from media_catalog. Returns
    {domain: {da, tf, cf, rd, price_eur, vertical, audience_tags,
    editorial_voice}}. Domains not in the catalog → empty result."""
    if not domains:
        return {}
    rows = db.execute(_text(
        """
        SELECT domain,
               MAX(da) AS da, MAX(tf) AS tf, MAX(cf) AS cf, MAX(rd) AS rd,
               MIN(price_eur) AS price_eur,
               (array_agg(DISTINCT v) FILTER (WHERE v IS NOT NULL))::text[] AS vertical,
               (array_agg(DISTINCT a) FILTER (WHERE a IS NOT NULL))::text[] AS audience_tags,
               (array_agg(DISTINCT editorial_voice) FILTER (WHERE editorial_voice IS NOT NULL))[1] AS editorial_voice
          FROM media_catalog
     LEFT JOIN LATERAL unnest(vertical) AS v ON true
     LEFT JOIN LATERAL unnest(audience_tags) AS a ON true
         WHERE domain = ANY(:d)
         GROUP BY domain
        """
    ), {"d": sorted(list(domains))}).fetchall()
    return {
        r.domain: {
            "da": r.da, "tf": r.tf, "cf": r.cf, "rd": r.rd,
            "price_eur": float(r.price_eur) if r.price_eur is not None else None,
            "vertical": list(r.vertical or []),
            "audience_tags": list(r.audience_tags or []),
            "editorial_voice": r.editorial_voice,
        }
        for r in rows
    }


def _classify(competitor_brands: list[str], target_cited: bool) -> str:
    """Map per-domain mention state to a sortable bucket."""
    has_competitor = bool(competitor_brands)
    if has_competitor and not target_cited:
        return "competitor_only"
    if has_competitor and target_cited:
        return "shared"
    return "target_only"


def _leverage_score(
    citation_count: int,
    competitor_count: int,
    target_cited: bool,
    da: int | None,
) -> int:
    """Composite 0-100. The shape mirrors Sprint 8's leverage so users see
    a comparable signal across action tabs.

      40 pts engagement  : log10(citation_count + 1) × 20, capped at 40
      30 pts breadth     : 1..N competitors at this domain → spans 0-30
      10 pts novelty     : target NOT also cited = pure opportunity
      20 pts authority   : Babbar DA when present (DA/5), 0 otherwise
    """
    cc = max(0, int(citation_count or 0))
    engagement = min(40, int(round(math.log10(cc + 1) * 25)))

    cb = max(0, int(competitor_count or 0))
    breadth = min(30, cb * 12)

    novelty = 10 if (cb > 0 and not target_cited) else 0

    auth = 0
    if da is not None:
        auth = max(0, min(20, int(round(da / 5))))

    return max(0, min(100, engagement + breadth + novelty + auth))


def _aggregate_domain(
    rows: list[dict],
    target_domain: str,
    competitor_domains: set[str],
    name_map: dict[str, str],
    target_name_lower: set[str],
    competitor_names_lower: set[str],
    site_type_by_domain: dict[str, str],
) -> dict[str, dict]:
    """Group raw citation rows by domain, dropping rows that are platform
    hosts, competitor own-sites, the focus brand's own site, or domains
    classified as Brand/E-commerce/Forum/Encyclopedia."""
    bucket: dict[str, dict] = {}

    for r in rows:
        domain = r["domain"]
        if not domain:
            continue
        if _is_platform_host(domain):
            continue
        # Drop the focus brand's own site (not a journalist).
        if target_domain and (domain == target_domain or domain.endswith("." + target_domain)):
            continue
        # Drop any competitor's own site.
        if any(domain == cd or domain.endswith("." + cd) for cd in competitor_domains if cd):
            continue
        site_type = site_type_by_domain.get(domain)
        if site_type in EXCLUDED_SITE_TYPES:
            continue

        url = r["url"]
        # Determine which brand_mentions in this response are competitors
        # vs target. brand_mentions[].est_marque_cible is the canonical flag.
        bm_list = r["brand_mentions"] or []
        slr_target_cited = False
        slr_competitors: set[str] = set()
        for bm in bm_list:
            name = (bm.get("brand_name") or "").strip()
            if not name:
                continue
            name_lower = name.lower()
            is_target_flag = bool(bm.get("est_marque_cible"))
            if is_target_flag or name_lower in target_name_lower:
                slr_target_cited = True
                continue
            # Sprint 9.1 fix : ONLY count brand_mentions whose name (or
            # alias) matches a brand classified as 'competitor' in this
            # scan. brand_mentions from the LLM contains far more than
            # competitors - ingredients (acide salicylique), drugs
            # (acitrétine), publication names (ameli.fr, frontiersin.org,
            # passeportsante.net), even random expert names ("Dr Henry
            # Morin Annick"). Without this filter the per-domain
            # competitor_count was 5-10× the real number, inflating the
            # leverage_score breadth term and the "+182" badge in the UI.
            if name_lower not in competitor_names_lower:
                continue
            slr_competitors.add(name_map.get(name_lower, name))

        b = bucket.get(domain)
        if b is None:
            b = {
                "domain": domain,
                "site_type": site_type,
                "citation_count": 0,
                "_competitor_brands": set(),
                "_target_cited": False,
                "_pages": {},
                "_winning_q": [],
                "_seen_q_keys": set(),
                "_count_keys": set(),
            }
            bucket[domain] = b

        # N-runs (T1) : count once per (question, provider, url) - the same
        # page cited across N runs of one question is one signal, not N.
        _ckey = (r["question_id"], r["provider"], url)
        if _ckey not in b["_count_keys"]:
            b["_count_keys"].add(_ckey)
            b["citation_count"] += 1
        b["_competitor_brands"].update(slr_competitors)
        if slr_target_cited:
            b["_target_cited"] = True

        # Sprint 9.1 fix : drop self-mention noise. brand_mentions may
        # contain the domain itself as a "brand" (e.g. ameli.fr citing
        # an article on ameli.fr produces a brand_mention 'ameli.fr' for
        # the publication). Strip it - a media never out-competes itself.
        if domain in slr_competitors:
            slr_competitors.discard(domain)
        # Also drop bare host stems like 'ameli' when the row IS ameli.fr.
        slr_competitors.discard(domain.split(".")[0])

        # Per-URL aggregation
        page = b["_pages"].get(url)
        if page is None:
            page = {
                "url": url,
                "title": r.get("title"),
                "citation_count": 0,
                "contexte": r.get("contexte") or "",
                "_competitors_set": set(),
                "target_cited": False,
                "_q_seen": set(),
                "_q_count": set(),
                "winning_questions": [],
            }
            b["_pages"][url] = page
        _pkey = (r["question_id"], r["provider"])
        if _pkey not in page["_q_count"]:
            page["_q_count"].add(_pkey)
            page["citation_count"] += 1
        page["_competitors_set"].update(slr_competitors)
        if slr_target_cited:
            page["target_cited"] = True
        # Keep the first non-empty contexte for the page.
        if not page["contexte"] and r.get("contexte"):
            page["contexte"] = r["contexte"]
        if not page["title"] and r.get("title"):
            page["title"] = r["title"]

        # Domain-level winning questions
        q_key = (r["question_id"], r["provider"])
        if r["question"] and q_key not in b["_seen_q_keys"]:
            b["_seen_q_keys"].add(q_key)
            if len(b["_winning_q"]) < MAX_WINNING_QUESTIONS_PER_DOMAIN:
                b["_winning_q"].append({
                    "question_id": r["question_id"],
                    "question": r["question"],
                    "provider": r["provider"],
                    "slr_id": r["slr_id"],
                })
        # Per-page winning questions
        if r["question"] and q_key not in page["_q_seen"]:
            page["_q_seen"].add(q_key)
            if len(page["winning_questions"]) < 5:
                page["winning_questions"].append({
                    "question_id": r["question_id"],
                    "question": r["question"],
                    "provider": r["provider"],
                })

    return bucket


def execute(job_payload: dict, scan_id: str, db: Session) -> dict:
    """Build the per-domain PR outreach list for this scan.

    job_payload :
      - reset (bool)         : drop existing rows before re-running
      - limit (int)          : cap domain count (default MAX_DOMAINS_PER_RUN)
    """
    from models import Scan, ScanPROutreach

    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan:
        raise RuntimeError("Scan not found")

    reset = bool(job_payload.get("reset"))
    limit = int(job_payload.get("limit") or MAX_DOMAINS_PER_RUN)

    if reset:
        db.query(ScanPROutreach).filter(ScanPROutreach.scan_id == scan_id).delete()
        db.commit()

    target_domain, competitor_domains, name_map, competitor_names_lower = _scan_brand_domains(db, scan_id)

    # Target-name lookup set : brand_mentions where est_marque_cible isn't
    # set on the row (legacy data) still get resolved as target if their
    # name matches one of the focus brand's known names/aliases.
    target_name_lower: set[str] = set()
    if scan.focus_brand and getattr(scan.focus_brand, "name", None):
        target_name_lower.add(scan.focus_brand.name.strip().lower())
        if getattr(scan.focus_brand, "canonical_name", None):
            target_name_lower.add(scan.focus_brand.canonical_name.strip().lower())
        for a in (scan.focus_brand.aliases or []):
            if a:
                target_name_lower.add(a.strip().lower())

    rows = _cited_pages_with_mentions(db, scan_id)
    if not rows:
        logger.info(f"build_pr_outreach: no citations for scan {scan_id}")
        return {"domains": 0, "rows_seen": 0}

    raw_domains = {r["domain"] for r in rows if r["domain"]}
    site_type_by_domain = _domain_classifications(db, raw_domains)

    bucket = _aggregate_domain(
        rows, target_domain, competitor_domains, name_map,
        target_name_lower, competitor_names_lower, site_type_by_domain,
    )
    if not bucket:
        logger.info(f"build_pr_outreach: no media domains after filtering for scan {scan_id}")
        return {"domains": 0, "rows_seen": len(rows)}

    catalog = _media_catalog_lookup(db, set(bucket.keys()))

    # Materialize, score, sort, cap. Drop domains that aren't actionable
    # for PR (no in-scope brand was ever mentioned in the LLM responses
    # citing them - they were context-only, not subject-of-the-piece).
    materialized: list[tuple[str, dict]] = []
    for domain, b in bucket.items():
        competitors_sorted = sorted(b["_competitor_brands"])
        target_cited = b["_target_cited"]
        if not competitors_sorted and not target_cited:
            continue
        # Sort pages : prefer competitor-only > shared > target-only, then
        # by citation_count desc.
        pages_list: list[dict] = []
        for p in b["_pages"].values():
            comps = sorted(p["_competitors_set"])
            pages_list.append({
                "url": p["url"],
                "title": p["title"],
                "citation_count": p["citation_count"],
                "contexte": p["contexte"],
                "competitor_brands": comps,
                "target_cited": p["target_cited"],
                "winning_questions": p["winning_questions"],
            })
        pages_list.sort(
            key=lambda p: (
                0 if (p["competitor_brands"] and not p["target_cited"]) else
                1 if (p["competitor_brands"] and p["target_cited"]) else 2,
                -p["citation_count"],
            )
        )
        top_pages = pages_list[:MAX_PAGES_PER_DOMAIN]

        cat = catalog.get(domain) or {}
        classification = _classify(competitors_sorted, target_cited)
        leverage = _leverage_score(
            b["citation_count"], len(competitors_sorted), target_cited, cat.get("da")
        )

        materialized.append((domain, {
            "site_type": b["site_type"],
            "citation_count": b["citation_count"],
            "competitor_brands": competitors_sorted,
            "target_cited": target_cited,
            "classification": classification,
            "top_pages": top_pages,
            "winning_questions": b["_winning_q"],
            "in_catalog": domain in catalog,
            "da": cat.get("da"),
            "tf": cat.get("tf"),
            "cf": cat.get("cf"),
            "rd": cat.get("rd"),
            "price_eur": cat.get("price_eur"),
            "vertical": cat.get("vertical") or [],
            "audience_tags": cat.get("audience_tags") or [],
            "editorial_voice": cat.get("editorial_voice"),
            "leverage_score": leverage,
        }))

    materialized.sort(key=lambda kv: -(kv[1]["leverage_score"] or 0))
    materialized = materialized[:limit]

    # Upsert. For idempotency we delete the existing row for the (scan,
    # domain) pair and re-insert with the fresh aggregates rather than
    # patch field-by-field - keeps the path obvious and the data fresh.
    domains_in_run = [d for d, _ in materialized]
    if domains_in_run:
        db.execute(_text(
            "DELETE FROM scan_pr_outreach WHERE scan_id = :s AND domain = ANY(:d)"
        ), {"s": scan_id, "d": domains_in_run})

    inserted = 0
    for domain, agg in materialized:
        db.add(ScanPROutreach(
            scan_id=scan_id,
            domain=domain,
            site_type=agg["site_type"],
            citation_count=agg["citation_count"],
            competitor_brands=agg["competitor_brands"],
            target_cited=agg["target_cited"],
            classification=agg["classification"],
            top_pages=agg["top_pages"],
            winning_questions=agg["winning_questions"],
            da=agg["da"],
            tf=agg["tf"],
            cf=agg["cf"],
            rd=agg["rd"],
            price_eur=agg["price_eur"],
            vertical=agg["vertical"],
            audience_tags=agg["audience_tags"],
            editorial_voice=agg["editorial_voice"],
            in_catalog=agg["in_catalog"],
            leverage_score=agg["leverage_score"],
        ))
        inserted += 1
        if inserted % 50 == 0:
            db.commit()

    db.commit()

    by_class = {"competitor_only": 0, "shared": 0, "target_only": 0}
    for _, agg in materialized:
        by_class[agg["classification"]] = by_class.get(agg["classification"], 0) + 1

    logger.info(
        f"build_pr_outreach: scan {scan_id} → {inserted} domains "
        f"(competitor_only={by_class['competitor_only']}, "
        f"shared={by_class['shared']}, target_only={by_class['target_only']})"
    )
    return {
        "domains": inserted,
        "rows_seen": len(rows),
        "by_classification": by_class,
    }
