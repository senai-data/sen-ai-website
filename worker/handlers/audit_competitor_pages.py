"""Handler : Sprint 7 competitor reverse-engineering.

For each scan we surface the top competitors by "win count" (questions
where they were mentioned by an LLM and the target brand was absent),
then audit the competitor pages the LLMs already cite for those wins.
Each page gets :
  - Princeton GEO patterns (shared analyzer from Sprint 5)
  - JSON-LD schemas (shared extractor from Sprint 6)
  - Babbar backlink authority lookup from media_catalog (MR.1)

Source of URLs : ONLY pages the LLMs already cite for the competitor
during this scan. No SERP API in v1 - we reverse-engineer what wins
right now, not the competitor's full inventory.

Caps :
  - 5 competitors max per scan
  - 10 URLs max per competitor
  - = 50 page fetches max per run, ~25 s on the wire

Cost : zero LLM. Plain HTTP + heuristic analyzers + DB lookups.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from sqlalchemy import text as _text
from sqlalchemy.orm import Session

from adapters.geo_pattern_analyzer import analyze_page
from config import settings
from adapters.page_fetcher import fetch_page
from adapters.schema_extractor import extract as extract_schemas
from adapters.schema_generator import detect_page_type, expected_schemas

logger = logging.getLogger(__name__)

PAGE_DELAY_SECONDS = 0.4
MAX_COMPETITORS = 5
MAX_URLS_PER_COMPETITOR = 10


def _top_competitors(db: Session, scan_id: str, limit: int) -> list[dict]:
    """Top competitors of the scan by 'win count'. A win is a scan_llm_result
    row where the competitor is mentioned (est_marque_cible=false on its
    mention) and the target brand is not mentioned at all in that response.

    Brand match : lower(brand_mentions.brand_name) ≈ lower(canonical_name) OR name.
    """
    sql = _text(
        """
        WITH ranked AS (
          SELECT cb.id AS brand_id,
                 cb.name,
                 cb.canonical_name,
                 cb.domain,
                 COUNT(DISTINCT slr.id) AS wins,
                 COUNT(DISTINCT slr.id) FILTER (
                   WHERE NOT EXISTS (
                     SELECT 1 FROM jsonb_array_elements(slr.brand_mentions) AS tbm
                     WHERE (tbm->>'est_marque_cible')::bool = true
                   )
                 ) AS solo_wins
            FROM scan_llm_results slr
            JOIN LATERAL jsonb_array_elements(slr.brand_mentions) AS bm ON true
            JOIN scan_brand_classifications sbc ON sbc.scan_id = slr.scan_id
            JOIN client_brands cb ON cb.id = sbc.brand_id
           WHERE slr.scan_id = :scan_id
             AND sbc.classification = 'competitor'
             AND (bm->>'est_marque_cible')::bool = false
             AND (
               lower(bm->>'brand_name') = lower(cb.canonical_name)
               OR lower(bm->>'brand_name') = lower(cb.name)
             )
           GROUP BY cb.id, cb.name, cb.canonical_name, cb.domain
        )
        SELECT brand_id, name, canonical_name, domain, wins, solo_wins
          FROM ranked
         WHERE domain IS NOT NULL AND domain != ''
         ORDER BY solo_wins DESC, wins DESC
         LIMIT :lim
        """
    )
    rows = db.execute(sql, {"scan_id": scan_id, "lim": limit}).fetchall()
    return [
        {
            "brand_id": str(r[0]),
            "name": r[1],
            "canonical_name": r[2],
            "domain": (r[3] or "").lower().lstrip("www.").strip("/"),
            "wins": int(r[4] or 0),
            "solo_wins": int(r[5] or 0),
        }
        for r in rows
    ]


def _competitor_urls(
    db: Session, scan_id: str, brand_domain: str, limit: int
) -> list[dict]:
    """Top URLs of the competitor domain cited by LLMs during this scan.

    Match : citation.domaine endswith brand_domain (handles www. prefix and
    subdomains : 'corporate.bioderma.com' matches 'bioderma.com').

    Returns rows with their winning_questions list (questions where the URL
    was cited).
    """
    if not brand_domain:
        return []
    sql = _text(
        """
        WITH cites AS (
          SELECT slr.id AS slr_id,
                 slr.question_id,
                 slr.provider,
                 -- Normalize URL : strip query string AND fragment so
                 -- variants like `?utm_source=openai` or `#section` don't
                 -- split the same page across multiple rows.
                 split_part(split_part(citation->>'url', '?', 1), '#', 1) AS url,
                 lower(citation->>'domaine') AS domaine,
                 citation->>'contexte' AS contexte
            FROM scan_llm_results slr,
                 LATERAL jsonb_array_elements(slr.citations) AS citation
           WHERE slr.scan_id = :scan_id
             AND (citation->>'est_site_cible')::bool = false
             AND citation->>'url' IS NOT NULL
             AND (
               lower(citation->>'domaine') = :dom
               OR lower(citation->>'domaine') LIKE :dom_suffix
             )
        )
        SELECT c.url,
               -- N-runs (T1) : one signal per (question, provider), not per run
               COUNT(DISTINCT (c.question_id, c.provider)) AS cites,
               jsonb_agg(DISTINCT jsonb_build_object(
                 'question_id', c.question_id::text,
                 'question',    sq.question,
                 'provider',    c.provider,
                 'contexte',    c.contexte,
                 'slr_id',      c.slr_id::text
               )) FILTER (WHERE sq.question IS NOT NULL) AS questions,
               array_agg(DISTINCT c.contexte) FILTER (WHERE c.contexte IS NOT NULL AND c.contexte != '') AS contextes
          FROM cites c
          LEFT JOIN scan_questions sq ON sq.id = c.question_id
         GROUP BY c.url
         ORDER BY cites DESC
         LIMIT :lim
        """
    )
    dom = brand_domain.lower()
    rows = db.execute(
        sql,
        {"scan_id": scan_id, "dom": dom, "dom_suffix": f"%.{dom}", "lim": limit},
    ).fetchall()
    return [
        {
            "url": r[0],
            "citation_count": int(r[1] or 0),
            "winning_questions": list(r[2] or []),
            "contextes": list(r[3] or []),
        }
        for r in rows
    ]


def _babbar_for_domain(db: Session, brand_domain: str, babbar_client=None) -> dict:
    """Look up Babbar authority signal in media_catalog. Returns the highest-
    quality row found for this domain (any locale). When the domain is not
    in media_catalog yet (Sprint 7.1), fall back to a live Babbar call and
    UPSERT the result so subsequent reads hit the cache.

    `babbar_client` is an optional BabbarClient instance to reuse across
    competitors in the same scan (avoids per-call setup + shares the
    in-memory rate-limit state). Pass None to disable the live fallback."""
    if not brand_domain:
        return {}
    sql = _text(
        """
        SELECT da, tf, cf, rd, babbar_last_check
          FROM media_catalog
         WHERE lower(domain) = :dom
            OR lower(domain) = :www_dom
         ORDER BY (da IS NOT NULL)::int DESC, da DESC NULLS LAST
         LIMIT 1
        """
    )
    row = db.execute(
        sql, {"dom": brand_domain.lower(), "www_dom": f"www.{brand_domain.lower()}"}
    ).fetchone()
    if row and row[0] is not None:
        return {
            "source": "media_catalog",
            "da": int(row[0]),
            "tf": int(row[1]) if row[1] is not None else None,
            "cf": int(row[2]) if row[2] is not None else None,
            "rd": int(row[3]) if row[3] is not None else None,
            "checked_at": row[4].isoformat() + "Z" if row[4] else None,
        }

    # Sprint 7.1 - live Babbar lookup for competitor domains absent du
    # media_catalog (or present but never enriched). One sync call per
    # competitor, ~1-3s + 6/min rate-limit pause. The result is UPSERT'd
    # so the next scan + the nightly media_catalog sweep both benefit.
    if babbar_client is None:
        return {"source": "none"}
    try:
        metrics = babbar_client.get_domain_metrics_cached(brand_domain)
    except Exception:
        logger.exception(f"audit_competitor_pages: Babbar live lookup crashed for {brand_domain}")
        return {"source": "none"}
    if not metrics or metrics.get("domainTrust") is None:
        return {"source": "none"}

    def _as_int(v):
        return int(v) if isinstance(v, (int, float)) else None
    da_val = _as_int(metrics.get("hostTrust"))
    tf_val = _as_int(metrics.get("domainTrust"))
    cf_val = _as_int(metrics.get("semanticValue"))
    rd_val = _as_int(metrics.get("backlinksCount"))

    # Best-effort UPSERT - failure here MUST NOT abort the parent audit.
    # Country / language unknown for competitor domains : 'XX' / 'xx' is
    # the convention the catalog sweep already uses for "unscoped".
    try:
        db.execute(_text(
            """
            INSERT INTO media_catalog (domain, country, language,
                                       da, tf, cf, rd,
                                       babbar_last_check, llm_citation_decayed,
                                       created_at, updated_at)
            VALUES (:d, 'XX', 'xx', :da, :tf, :cf, :rd, NOW(), 0,
                    NOW(), NOW())
            ON CONFLICT (domain, country, language) DO UPDATE
               SET da = EXCLUDED.da,
                   tf = EXCLUDED.tf,
                   cf = EXCLUDED.cf,
                   rd = EXCLUDED.rd,
                   babbar_last_check = NOW(),
                   updated_at = NOW()
            """
        ), {
            "d": brand_domain.lower(),
            "da": da_val, "tf": tf_val, "cf": cf_val, "rd": rd_val,
        })
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            f"audit_competitor_pages: media_catalog UPSERT failed for {brand_domain}"
        )

    return {
        "source": "babbar_live",
        "da": da_val,
        "tf": tf_val,
        "cf": cf_val,
        "rd": rd_val,
        "checked_at": datetime.utcnow().isoformat() + "Z",
    }


def _audit_from_contextes(contextes: list[str], url: str, page_domain: str) -> dict:
    """Fallback Princeton audit derived from the LLM citation snippets when
    the live page is blocked to crawlers (401/403/429/503).

    The snippets are the actual text the LLM saw around the citation, so
    they're a legitimate partial signal - thinner than a full page audit
    but the same dimensions (statistics, citations, quotations, etc.).
    We wrap them as synthetic HTML and reuse the same `analyze_page`
    pipeline so the JSONB shape matches a normal audit and the UI can
    render it identically.

    The result is marked ``source="contexte"`` so the API + UI know to
    label it differently (smaller-than-real word_count, "based on LLM
    snippets" tooltip).
    """
    cleaned = [c for c in (contextes or []) if c and c.strip()]
    if not cleaned:
        return {}
    # Wrap each snippet as a paragraph so analyze_page treats them as
    # distinct sentences for fluency / readability metrics.
    body = "\n".join(f"<p>{c}</p>" for c in cleaned)
    fake_html = (
        "<html><head><title>LLM citation snippets</title></head>"
        f"<body><article>{body}</article></body></html>"
    )
    try:
        result = analyze_page(fake_html, url, page_domain=page_domain)
    except Exception:  # noqa: BLE001
        logger.exception(f"contexte fallback analyze failed for {url}")
        return {}
    result["source"] = "contexte"
    return result


def _schema_score(page_type: str, schemas: list[dict], expected: list[str]) -> int:
    """Lightweight schema score mirror of Sprint 6 weights, kept inline to
    avoid a worker-handler-to-handler import. Identical formula."""
    if not schemas and not expected:
        return 0
    have = {s["type"] for s in schemas if s.get("valid")}
    weights_used = 0
    earned = 0
    weights_used += 25
    if "Organization" in have:
        earned += 25
    primary = {"article": "Article", "product": "Product", "faq": "FAQPage"}.get(page_type)
    if primary:
        weights_used += 25
        if primary in have:
            earned += 25
    if "BreadcrumbList" in expected:
        weights_used += 20
        if "BreadcrumbList" in have:
            earned += 20
    if schemas:
        weights_used += 20
        valid_count = sum(1 for s in schemas if s.get("valid"))
        earned += int(20 * valid_count / len(schemas))
    if page_type == "homepage":
        weights_used += 10
        if "WebSite" in have:
            earned += 10
    if weights_used == 0:
        return 0
    return max(0, min(100, round(100 * earned / weights_used)))


def execute(job_payload: dict, scan_id: str, db: Session) -> dict:
    """Audit the top competitors' cited pages.

    job_payload :
      - reset (bool) : drop existing rows for this scan before re-running
      - competitors (int) : cap on top competitors (default MAX_COMPETITORS)
      - urls_per_competitor (int) : cap on URLs per competitor (default 10)
    """
    from models import Scan, ScanCompetitorPage

    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan:
        raise RuntimeError("Scan not found")

    reset = bool(job_payload.get("reset"))
    n_competitors = int(job_payload.get("competitors") or MAX_COMPETITORS)
    n_urls = int(job_payload.get("urls_per_competitor") or MAX_URLS_PER_COMPETITOR)

    if reset:
        db.query(ScanCompetitorPage).filter(ScanCompetitorPage.scan_id == scan_id).delete()
        db.commit()

    competitors = _top_competitors(db, scan_id, n_competitors)
    if not competitors:
        logger.info(f"audit_competitor_pages: no competitor wins for scan {scan_id}")
        return {"competitors": 0, "audited": 0, "errors": 0}

    # Sprint 7.1 - one BabbarClient shared across competitors so the
    # in-memory rate-limit state stays consistent + we don't re-load env
    # vars 5 times. None if Babbar isn't configured -> live fallback
    # silently degrades to the existing media-catalog-only behaviour.
    babbar_client = None
    try:
        from seo_llm.src.babbar_client import BabbarClient
        candidate = BabbarClient()
        if candidate.api_key:
            babbar_client = candidate
    except Exception:
        logger.exception("audit_competitor_pages: Babbar client init failed, skipping live enrichment")

    audited = 0
    errors = 0
    skipped = 0

    for comp in competitors:
        brand_domain = comp["domain"]
        backlinks = _babbar_for_domain(db, brand_domain, babbar_client=babbar_client)

        urls = _competitor_urls(db, scan_id, brand_domain, n_urls)
        if not urls:
            continue

        for u in urls:
            url = u["url"]
            if not url or not url.startswith(("http://", "https://")):
                skipped += 1
                continue

            fetched = fetch_page(url)
            status = fetched["status"]
            err = fetched["error"]
            html = fetched["html"]
            title = None
            geo_payload: dict = {}
            geo_score = None
            schemas: list[dict] = []
            schema_score_val = None

            if html and not err:
                try:
                    soup = BeautifulSoup(html, "html.parser")
                    if soup.title and soup.title.string:
                        title = soup.title.string.strip()[:300]

                    # Princeton GEO heuristics - same analyzer as Sprint 5.
                    result = analyze_page(html, url, page_domain=brand_domain)
                    geo_score = result.get("geo_score")
                    geo_payload = {
                        "source":  "page",
                        "signals": result.get("signals", {}),
                        "scores":  result.get("scores", {}),
                        "issues":  result.get("issues", []),
                    }

                    # JSON-LD schemas - same extractor as Sprint 6.
                    schemas = extract_schemas(html)
                    page_type = detect_page_type(url, html, soup)
                    expected = expected_schemas(page_type, url)
                    schema_score_val = _schema_score(page_type, schemas, expected)
                except Exception:  # noqa: BLE001
                    logger.exception(f"competitor audit analyze failed for {url}")
                    err = err or "analyze_error"
                    errors += 1
            elif err and err.startswith("blocked_http_"):
                # Fallback : the page is blocked to crawlers but we already
                # have the snippets the LLM used when citing it. Reuse the
                # Princeton analyzer on those snippets so the user still
                # gets a (qualified) signal instead of an empty card.
                fallback = _audit_from_contextes(u.get("contextes", []), url, brand_domain)
                if fallback:
                    geo_score = fallback.get("geo_score")
                    geo_payload = {
                        "source":  "contexte",
                        "signals": fallback.get("signals", {}),
                        "scores":  fallback.get("scores", {}),
                        "issues":  fallback.get("issues", []),
                    }
                errors += 1
            else:
                errors += 1

            existing = (
                db.query(ScanCompetitorPage)
                .filter(
                    ScanCompetitorPage.scan_id == scan_id,
                    ScanCompetitorPage.brand_id == comp["brand_id"],
                    ScanCompetitorPage.url == url,
                )
                .first()
            )
            if existing:
                existing.title = title or existing.title
                existing.fetch_status = status
                existing.fetch_error = err
                existing.citation_count = u["citation_count"]
                existing.winning_questions = u["winning_questions"]
                existing.geo_audit = geo_payload
                existing.geo_score = geo_score
                existing.schemas = schemas
                existing.schema_score = schema_score_val
                existing.backlinks = backlinks
            else:
                db.add(ScanCompetitorPage(
                    scan_id=scan_id,
                    brand_id=comp["brand_id"],
                    url=url,
                    title=title,
                    fetch_status=status,
                    fetch_error=err,
                    citation_count=u["citation_count"],
                    winning_questions=u["winning_questions"],
                    geo_audit=geo_payload,
                    geo_score=geo_score,
                    schemas=schemas,
                    schema_score=schema_score_val,
                    backlinks=backlinks,
                ))

            audited += 1
            if audited % 10 == 0:
                db.commit()
                logger.info(f"competitor audit progress {audited}")

            time.sleep(PAGE_DELAY_SECONDS)

    db.commit()

    # Sprint 7.2 - LLM recommendation card. After we've audited the
    # competitor pages we have enough delta vs the user's own pages to
    # ask Haiku for 3 specific "what to change" hints per competitor.
    # Budget capped at $0.02 / scan (~5 competitors × $0.004 max) so
    # this never balloons.
    try:
        recos = _generate_competitor_recommendations(db, scan, scan_id)
        if recos:
            summary = dict(scan.summary or {})
            summary["competitor_recommendations"] = recos
            from datetime import datetime as _dt
            summary["competitor_recommendations_generated_at"] = _dt.utcnow().isoformat() + "Z"
            scan.summary = summary
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(scan, "summary")
            db.commit()
    except Exception:
        logger.exception("competitor recommendations generation failed - non-fatal")

    logger.info(
        f"competitor audit complete : competitors={len(competitors)} "
        f"audited={audited} errors={errors} skipped={skipped}"
    )
    return {
        "competitors": len(competitors),
        "audited": audited,
        "errors": errors,
        "skipped": skipped,
    }


_LLM_RECO_MODEL = "claude-haiku-4-5-20251001"
_LLM_RECO_BUDGET_USD = 0.02
_LLM_RECO_PROMPT = """You audit a brand competing with {own_brand} on AI search visibility.

COMPETITOR: {comp_brand} ({comp_domain})
- {comp_n_pages} cited pages audited
- Average GEO score (Princeton heuristics, 0-100): {comp_geo}
- Average Schema.org JSON-LD score (0-100): {comp_schema}
- Schema types they use: {comp_schemas}
- Babbar DA: {comp_da}

YOUR BRAND: {own_brand}
- Average GEO score: {own_geo}
- Average Schema score: {own_schema}
- Schema types you use: {own_schemas}

Reply with JSON only:
{{
  "recommendations": [
    {{"title": "short imperative", "rationale": "one sentence why", "effort": "low|medium|high"}}
  ]
}}

Rules:
- Exactly 3 recommendations
- Each "title" <= 8 words, action verb first
- "rationale" cites a specific delta from the audit numbers above
- "effort" reflects implementation cost from the user's side
- No generic SEO advice ; tie each reco to the audit data you were given
- French is OK if domains are .fr"""


def _generate_competitor_recommendations(db, scan, scan_id: str) -> dict:
    """Per top competitor, ask Haiku for 3 actionable recommendations
    grounded in the audit delta. Returns dict keyed by brand_id with
    {recommendations: [...], comp_brand_name, comp_domain}."""
    from services.byok import resolve_anthropic_key
    api_key, key_source = resolve_anthropic_key(db, scan.client_id)
    api_key = (api_key or "").strip()
    if not api_key:
        logger.info("competitor recommendations: no anthropic_api_key, skipping")
        return {}

    import httpx as _httpx
    import json as _json
    from datetime import datetime as _dt
    from models import ScanCompetitorPage, ScanPageAudit, ScanSchemaAudit, ClientBrand

    own_pages = db.query(ScanPageAudit).filter(ScanPageAudit.scan_id == scan_id).all()
    own_schemas = db.query(ScanSchemaAudit).filter(ScanSchemaAudit.scan_id == scan_id).all()
    own_geo_avg = None
    if own_pages:
        s = [p.geo_score for p in own_pages if p.geo_score is not None]
        own_geo_avg = round(sum(s) / len(s), 1) if s else None
    own_schema_avg = None
    if own_schemas:
        s = [x.schema_score for x in own_schemas if x.schema_score is not None]
        own_schema_avg = round(sum(s) / len(s), 1) if s else None
    own_schema_types: dict[str, int] = {}
    for ss in own_schemas:
        for b in (ss.existing_schemas or []):
            if b.get("valid") and b.get("type"):
                own_schema_types[b["type"]] = own_schema_types.get(b["type"], 0) + 1

    own_brand_name = None
    if scan.focus_brand_id:
        fb = db.query(ClientBrand).filter(ClientBrand.id == scan.focus_brand_id).first()
        own_brand_name = fb.name if fb else None
    own_brand_name = own_brand_name or scan.domain or "your brand"

    comp_pages = db.query(ScanCompetitorPage, ClientBrand).join(
        ClientBrand, ClientBrand.id == ScanCompetitorPage.brand_id
    ).filter(ScanCompetitorPage.scan_id == scan_id).all()
    by_brand: dict[str, dict] = {}
    for row, brand in comp_pages:
        bucket = by_brand.setdefault(str(brand.id), {
            "brand_name": brand.name, "domain": brand.domain,
            "geo": [], "schema": [], "schema_types": {}, "da": None,
        })
        if row.geo_score is not None:
            bucket["geo"].append(row.geo_score)
        if row.schema_score is not None:
            bucket["schema"].append(row.schema_score)
        for b in (row.schemas or []):
            if b.get("valid") and b.get("type"):
                bucket["schema_types"][b["type"]] = bucket["schema_types"].get(b["type"], 0) + 1
        if bucket["da"] is None and (row.backlinks or {}).get("da"):
            bucket["da"] = row.backlinks["da"]

    out: dict[str, dict] = {}
    spent = 0.0
    for bid, agg in by_brand.items():
        if spent >= _LLM_RECO_BUDGET_USD:
            logger.info(f"competitor recommendations: budget {_LLM_RECO_BUDGET_USD} reached, stopping")
            break
        if len(agg["geo"]) < 3:
            continue
        comp_geo_avg = round(sum(agg["geo"]) / len(agg["geo"]), 1)
        comp_schema_avg = round(sum(agg["schema"]) / len(agg["schema"]), 1) if agg["schema"] else None
        prompt = _LLM_RECO_PROMPT.format(
            own_brand=own_brand_name,
            comp_brand=agg["brand_name"],
            comp_domain=agg["domain"] or "-",
            comp_n_pages=len(agg["geo"]),
            comp_geo=comp_geo_avg,
            comp_schema=comp_schema_avg if comp_schema_avg is not None else "n/a",
            comp_schemas=", ".join(sorted(agg["schema_types"].keys())) or "none",
            comp_da=agg["da"] if agg["da"] is not None else "n/a",
            own_geo=own_geo_avg if own_geo_avg is not None else "n/a",
            own_schema=own_schema_avg if own_schema_avg is not None else "n/a",
            own_schemas=", ".join(sorted(own_schema_types.keys())) or "none",
        )
        try:
            resp = _httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": _LLM_RECO_MODEL,
                    "max_tokens": 600,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            usage = data.get("usage", {}) or {}
            cost = (usage.get("input_tokens", 0) / 1_000_000 * 0.80) + (usage.get("output_tokens", 0) / 1_000_000 * 4.00)
            spent += cost
            # Usage logging (was a gap until BYOK - the daily + monthly caps
            # both undercounted these Haiku recos).
            from adapters.llm_logger import log_llm_usage
            log_llm_usage(
                db, provider="anthropic", model=_LLM_RECO_MODEL,
                operation="competitor_recommendations",
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cost_usd=cost,
                scan_id=str(scan_id), client_id=str(scan.client_id),
                key_source=key_source,
            )
        except Exception:
            logger.exception(f"competitor recommendations: Haiku call failed for brand {bid}")
            continue
        # Parse JSON. Be tolerant : strip code fences if any.
        t = text.strip()
        if t.startswith("```"):
            t = t.strip("`")
            # remove "json" leader
            t = t.split("\n", 1)[1] if "\n" in t else t
            t = t.rstrip("`").strip()
        try:
            parsed = _json.loads(t)
            recs = parsed.get("recommendations") or []
            if recs:
                out[bid] = {
                    "brand_name": agg["brand_name"],
                    "domain": agg["domain"],
                    "recommendations": recs[:3],
                    "generated_at": _dt.utcnow().isoformat() + "Z",
                    "model": _LLM_RECO_MODEL,
                }
        except Exception:
            logger.warning(f"competitor recommendations: bad JSON for brand {bid}: {t[:200]}")
            continue
    return out
