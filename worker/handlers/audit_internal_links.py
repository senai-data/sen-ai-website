"""Handler : Sprint 11 internal linking audit.

For each URL of the user's own site that an LLM cited during this scan,
we re-fetch the HTML, parse every <a href> on the page, classify each
link as internal/external + generic-anchor/specific-anchor, and persist
the parsed graph + a per-page linking_score.

Topology stats (orphans / hubs / dead-ends) are computed at READ time
from the persisted outbound_internal_links arrays - the API endpoint
runs the aggregate so this handler stays a pure per-page fetch + parse.

Source of URLs : same set as Sprint 5 (Princeton GEO audit), i.e. URLs
that appear in scan_llm_results.citations[] with est_site_cible=true.

Cost : zero LLM. Plain HTTP + BeautifulSoup + a regex generic-anchor
detector. ~1-2 s per page over the wire, capped at 200 URLs / run.
"""
from __future__ import annotations

import logging
import time
from urllib.parse import urlparse, urljoin

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session
from sqlalchemy import text as _text

from adapters.page_fetcher import fetch_page

logger = logging.getLogger(__name__)

PAGE_DELAY_SECONDS = 0.4
MAX_URLS_PER_RUN = 200
MAX_LINKS_PER_PAGE = 200      # cap on stored outbound entries per row
MAX_ANCHOR_LEN = 200          # truncate runaway anchor blobs before storing
MIN_CITATION_COUNT = 1

# Generic / no-semantic anchor phrases across the languages we support. The
# user can extend via Domain Brief in a future sprint ; for v1 this is a
# heuristic cover for the obvious cases. The match is whole-word, case-
# insensitive, exact match on the trimmed anchor text.
GENERIC_ANCHORS = {
    # English
    "click here", "click", "here", "read more", "more", "more info", "more information",
    "learn more", "find out more", "see more", "see here", "view more", "view",
    "this", "this link", "this page", "this article", "link", "the link",
    "source", "sources", "reference", "references", "read the article",
    "details", "more details", "go", "go here", "go to", "see all",
    "discover", "discover more", "shop", "shop now", "buy now", "explore",
    "open", "download", "open the page",
    # French
    "cliquez ici", "cliquer ici", "ici", "lire la suite", "lire plus",
    "lire l'article", "voir plus", "voir ici", "voir tous", "voir tout",
    "en savoir plus", "savoir plus", "details", "détails", "plus de détails",
    "lien", "le lien", "ce lien", "cette page", "source", "sources",
    "référence", "références", "découvrir", "découvrez", "ouvrir",
    "télécharger", "acheter", "achetez", "acheter maintenant",
    # Spanish, Italian, German - light coverage for multilingual sites
    "haga clic aquí", "aquí", "leer más", "más info",
    "clicca qui", "qui", "leggi di più", "leggi tutto",
    "hier klicken", "hier", "mehr lesen", "weiterlesen", "mehr",
}


def _normalize_anchor(text: str | None) -> str:
    """Strip whitespace + collapse runs. Returns lowercased version for
    comparisons ; caller keeps the original case for display."""
    if not text:
        return ""
    t = " ".join(text.split())
    return t[:MAX_ANCHOR_LEN]


def _is_generic_anchor(anchor_lower: str) -> bool:
    """True when the anchor text alone tells the reader nothing about the
    target. Whole-string match against GENERIC_ANCHORS (after trimming
    punctuation)."""
    if not anchor_lower:
        return False
    stripped = anchor_lower.strip(" .,:;!?\"'()[]→›»>«<")
    return stripped in GENERIC_ANCHORS


def _link_position(a_tag) -> str | None:
    """Walk parents to classify the link's structural position. We label
    main / nav / footer based on standard semantic tags + common class
    name conventions. Anything else returns None (rendered as 'body')."""
    cur = a_tag
    while cur is not None and getattr(cur, "name", None) is not None:
        name = (cur.name or "").lower()
        if name in ("nav", "header"):
            return "nav"
        if name == "footer":
            return "footer"
        if name == "main" or name == "article":
            return "main"
        cls = " ".join(cur.get("class") or [])
        cls_low = cls.lower()
        if any(k in cls_low for k in ("navbar", "nav-", "menu", "breadcrumb")):
            return "nav"
        if any(k in cls_low for k in ("footer", "site-footer")):
            return "footer"
        cur = cur.parent
    return None


def _parse_outbound_links(html: str, source_url: str, primary_host: str) -> dict:
    """Parse every <a href> on the page. Returns :
        {
          internal: [{target, anchor, anchor_lower, is_generic, is_empty,
                      is_image, rel, position}, ...],
          external_count: int,
        }
    Internal = same primary host as source_url (after canonicalizing
    'www.' prefix). External links are counted but not stored.

    Skips javascript:, mailto:, tel:, #fragment-only, and same-page anchors.
    """
    out_internal: list[dict] = []
    external_count = 0

    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a"):
        href = (a.get("href") or "").strip()
        if not href:
            continue
        low = href.lower()
        if low.startswith(("javascript:", "mailto:", "tel:", "sms:", "#")):
            continue

        try:
            absolute = urljoin(source_url, href)
        except Exception:
            continue
        target_parsed = urlparse(absolute)
        if not target_parsed.scheme.startswith("http"):
            continue
        target_host = (target_parsed.netloc or "").lower()
        if target_host.startswith("www."):
            target_host = target_host[4:]
        # Skip same-URL anchors (e.g. /current-page#section already filtered
        # above by '#', but #section-only with no path repeats the canonical).
        if target_parsed.netloc == "" or target_host == "":
            continue
        if target_host != primary_host:
            external_count += 1
            continue

        # Anchor + img-alt fallback.
        anchor_raw = a.get_text(" ", strip=True) or ""
        is_image = False
        if not anchor_raw:
            img = a.find("img")
            if img:
                is_image = True
                anchor_raw = (img.get("alt") or "").strip()

        anchor = _normalize_anchor(anchor_raw)
        anchor_lower = anchor.lower()
        is_empty = (anchor == "")
        is_generic = (not is_empty) and _is_generic_anchor(anchor_lower)
        rel = " ".join(a.get("rel") or []) if a.get("rel") else ""

        out_internal.append({
            "target": absolute,
            "anchor": anchor,
            "anchor_lower": anchor_lower,
            "is_generic": is_generic,
            "is_empty": is_empty,
            "is_image": is_image,
            "rel": rel,
            "position": _link_position(a),
        })
        if len(out_internal) >= MAX_LINKS_PER_PAGE:
            break

    return {"internal": out_internal, "external_count": external_count}


def _cited_urls(db: Session, scan_id: str) -> list[tuple[str, int]]:
    """Mirror of audit_scan_pages._cited_urls. Returns URLs of the user's
    own site that LLMs cite, with citation counts."""
    sql = _text(
        """
        SELECT citation->>'url' AS url, COUNT(*)::int AS n
          FROM scan_llm_results slr,
               LATERAL jsonb_array_elements(slr.citations) AS citation
         WHERE slr.scan_id = :scan_id
           AND (citation->>'est_site_cible')::bool = true
           AND citation->>'url' IS NOT NULL
         GROUP BY citation->>'url'
        HAVING COUNT(*) >= :min_cnt
         ORDER BY n DESC
         LIMIT :lim
        """
    )
    rows = db.execute(
        sql,
        {"scan_id": scan_id, "min_cnt": MIN_CITATION_COUNT, "lim": MAX_URLS_PER_RUN},
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _normalize_primary_host(domain_raw: str) -> str:
    """Lower + strip 'www.' + strip scheme + strip trailing path."""
    if not domain_raw:
        return ""
    if "://" in domain_raw:
        d = urlparse(domain_raw).netloc or ""
    else:
        d = domain_raw
    d = (d or "").lower().strip()
    if d.startswith("www."):
        d = d[4:]
    return d.rstrip("/").split("/")[0]


def _build_issues(
    internal_links: list[dict], duplicate_groups: list[dict]
) -> list[dict]:
    """Emit one issue entry per generic / empty / duplicate anchor case.
    Bounded so a 100-link page doesn't produce 100 issues."""
    issues: list[dict] = []
    MAX_ISSUES = 30

    generic_seen = set()
    for link in internal_links:
        if len(issues) >= MAX_ISSUES:
            break
        if link["is_generic"]:
            key = (link["anchor_lower"], link["target"])
            if key in generic_seen:
                continue
            generic_seen.add(key)
            issues.append({
                "type": "generic_anchor",
                "severity": "medium",
                "anchor": link["anchor"],
                "target": link["target"],
                "message": (
                    f"Anchor '{link['anchor']}' carries no topical signal. "
                    f"LLMs and search engines learn what the target page is about from anchor "
                    f"context - replace with 2-5 descriptive words."
                ),
            })

    for link in internal_links:
        if len(issues) >= MAX_ISSUES:
            break
        if link["is_empty"]:
            issues.append({
                "type": "empty_anchor",
                "severity": "high" if link["is_image"] else "medium",
                "anchor": "",
                "target": link["target"],
                "message": (
                    "Image link with empty alt text - the LLM (and screen readers) "
                    "see no anchor. Add alt='descriptive label' to the inner <img>."
                ) if link["is_image"] else (
                    "Link has no anchor text - invisible to LLMs that learn from anchors. "
                    "Add 2-5 descriptive words."
                ),
            })

    for dup in duplicate_groups[:5]:
        if len(issues) >= MAX_ISSUES:
            break
        issues.append({
            "type": "duplicate_anchor",
            "severity": "low",
            "anchor": dup["anchor"],
            "targets": dup["targets"],
            "message": (
                f"Anchor '{dup['anchor']}' points to {len(dup['targets'])} different "
                f"internal pages. LLMs can't disambiguate which page wins the topical "
                f"authority - rephrase each link uniquely."
            ),
        })

    return issues


def _find_duplicate_anchor_groups(internal_links: list[dict]) -> list[dict]:
    """Returns [{anchor, targets[]}] for each anchor text used on >1 distinct
    internal target. Skips empty + generic anchors (those are reported as
    their own issue types)."""
    by_anchor: dict[str, set[str]] = {}
    for link in internal_links:
        if link["is_empty"] or link["is_generic"]:
            continue
        a = link["anchor_lower"]
        if not a:
            continue
        by_anchor.setdefault(a, set()).add(link["target"])
    groups: list[dict] = []
    for anchor_low, targets in by_anchor.items():
        if len(targets) > 1:
            # Recover the first-seen original-case anchor for display.
            display = anchor_low
            for link in internal_links:
                if link["anchor_lower"] == anchor_low and link["anchor"]:
                    display = link["anchor"]
                    break
            groups.append({"anchor": display, "targets": sorted(list(targets))})
    return groups


def _linking_score(
    internal_count: int,
    generic_count: int,
    empty_count: int,
    avg_anchor_length: float,
    unique_targets: int,
) -> int:
    """Composite 0-100. See migration comment for the formula breakdown."""
    if internal_count == 0:
        # Dead end. Capped baseline at 30 since a sitewide-orphan page is
        # actively harmful for link equity flow.
        return 30

    # Anchor quality (40 pts) : 1 - generic_ratio - empty_ratio_penalty
    generic_ratio = generic_count / max(1, internal_count)
    empty_ratio = empty_count / max(1, internal_count)
    quality = max(0.0, 1.0 - generic_ratio - 0.5 * empty_ratio)
    quality_pts = int(round(quality * 40))

    # Diversity (30 pts) : avg anchor length × unique-targets ratio.
    # avg_len of 30+ chars (e.g. "Discover the Avene Cicalfate hydrating routine")
    # and unique_targets close to internal_count → max points.
    len_factor = min(1.0, (avg_anchor_length or 0) / 30.0)
    div_factor = min(1.0, unique_targets / max(1, internal_count))
    diversity_pts = int(round(len_factor * div_factor * 30))

    # Depth (15 pts) : log-saturating at 8 internal links.
    depth_pts = min(15, int(round((internal_count / 8) * 15)))

    # Dead-end penalty (15 pts) : already handled (return 30 early).
    deadend_pts = 15

    return max(0, min(100, quality_pts + diversity_pts + depth_pts + deadend_pts))


def execute(job_payload: dict, scan_id: str, db: Session) -> dict:
    """Audit the internal link graph of pages the user's own site is cited
    on in this scan.

    job_payload :
      - limit (int) : cap URLs audited (default MAX_URLS_PER_RUN)
      - reset (bool): drop existing rows before re-running
    """
    from models import Scan, ScanInternalLink

    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan:
        raise RuntimeError("Scan not found")

    limit = int(job_payload.get("limit") or MAX_URLS_PER_RUN)
    reset = bool(job_payload.get("reset"))

    if reset:
        db.query(ScanInternalLink).filter(ScanInternalLink.scan_id == scan_id).delete()
        db.commit()

    pairs = _cited_urls(db, scan_id)[:limit]
    if not pairs:
        logger.info(f"audit_internal_links: no cited URLs for scan {scan_id}")
        return {"audited": 0, "errors": 0, "skipped": 0, "total": 0}

    primary_host = _normalize_primary_host(scan.domain or "")

    audited = 0
    errors = 0
    skipped = 0

    for idx, (url, cited_count) in enumerate(pairs):
        if not url or not url.startswith(("http://", "https://")):
            skipped += 1
            continue

        # Use the source URL's host as fallback when the scan has no domain
        # configured - rare but defensible (the page itself is the truth).
        url_host = _normalize_primary_host(url)
        host_for_link_filter = primary_host or url_host

        fetched = fetch_page(url)
        html = fetched["html"]
        status = fetched["status"]
        err = fetched["error"]
        title = None
        internal_links: list[dict] = []
        external_count = 0

        if html and not err:
            try:
                soup_title = BeautifulSoup(html[:32000], "html.parser").find("title")
                if soup_title and soup_title.string:
                    title = soup_title.string.strip()[:300]
                parsed = _parse_outbound_links(html, url, host_for_link_filter)
                internal_links = parsed["internal"]
                external_count = parsed["external_count"]
            except Exception:
                logger.exception(f"audit_internal_links parse failed for {url}")
                errors += 1
                err = "parse_error"
        else:
            errors += 1

        internal_count = len(internal_links)
        generic_count = sum(1 for l in internal_links if l["is_generic"])
        empty_count = sum(1 for l in internal_links if l["is_empty"])
        unique_targets = len({l["target"] for l in internal_links})
        if internal_count > 0:
            anchor_lengths = [len(l["anchor"]) for l in internal_links if not l["is_empty"]]
            avg_anchor_length = (
                sum(anchor_lengths) / len(anchor_lengths)
                if anchor_lengths else 0.0
            )
        else:
            avg_anchor_length = None

        duplicate_groups = _find_duplicate_anchor_groups(internal_links)
        duplicate_count = sum(len(g["targets"]) for g in duplicate_groups)
        issues = _build_issues(internal_links, duplicate_groups)

        linking_score = _linking_score(
            internal_count, generic_count, empty_count,
            avg_anchor_length or 0.0, unique_targets,
        )

        existing = (
            db.query(ScanInternalLink)
            .filter(ScanInternalLink.scan_id == scan_id, ScanInternalLink.url == url)
            .first()
        )
        if existing:
            existing.title = title or existing.title
            existing.fetched_at = _utcnow()
            existing.fetch_status = status
            existing.fetch_error = err
            existing.outbound_internal_count = internal_count
            existing.outbound_external_count = external_count
            existing.generic_anchor_count = generic_count
            existing.empty_anchor_count = empty_count
            existing.duplicate_anchor_count = duplicate_count
            existing.avg_anchor_length = avg_anchor_length
            existing.outbound_internal_links = internal_links
            existing.issues = issues
            existing.linking_score = linking_score
            existing.citation_count = cited_count
        else:
            db.add(ScanInternalLink(
                scan_id=scan_id,
                url=url,
                title=title,
                fetch_status=status,
                fetch_error=err,
                outbound_internal_count=internal_count,
                outbound_external_count=external_count,
                generic_anchor_count=generic_count,
                empty_anchor_count=empty_count,
                duplicate_anchor_count=duplicate_count,
                avg_anchor_length=avg_anchor_length,
                outbound_internal_links=internal_links,
                issues=issues,
                linking_score=linking_score,
                citation_count=cited_count,
            ))

        audited += 1
        if audited % 25 == 0:
            db.commit()
            logger.info(
                f"audit_internal_links progress {audited}/{len(pairs)} "
                f"(errors={errors}, skipped={skipped})"
            )
        time.sleep(PAGE_DELAY_SECONDS)

    db.commit()
    logger.info(
        f"audit_internal_links complete : audited={audited} errors={errors} "
        f"skipped={skipped} total={len(pairs)}"
    )
    return {
        "audited": audited,
        "errors": errors,
        "skipped": skipped,
        "total": len(pairs),
    }


def _utcnow():
    from datetime import datetime
    return datetime.utcnow()
