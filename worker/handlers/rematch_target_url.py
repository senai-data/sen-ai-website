"""Handler: rerun the target_url matcher on a single content item, skipping
URLs the user has already rejected.

Triggered when the user clicks "Find a different page" / "Find a different
media partner" on the validation page. Routes by content_type :

**FAQ items** (content_type='faq')
  The current `target_url` (if any) is appended to `rejected_target_urls`,
  then we cascade :
    1. **Sitemap-index semantic matcher** (Phase D) — same as the
       materialize pass, but with rejected_target_urls passed as
       exclude_urls. When the next-best page scores >= SITEMAP_THRESHOLD
       we take it (source='sitemap_index', candidates persisted).
    2. **ExcludingFAQPageMatcher web_search** (legacy) — used when sitemap
       returns nothing or scores below threshold. Source='auto_suggest'.

**Netlinking article items** (content_type='netlinking_article')
  Routed to `services.media_picker.pick_media_candidates` with
  `exclude_domains` derived from rejected_target_urls. Returns a fresh
  top-3 of media partners (citation-driven discovery + LinkFinder).
  Source='media_picker'. No FAQ matchers run — they don't apply to
  third-party media.

Same target_site/brand resolution as materialize_content_items, so the
competitor-scan brand-bias rule still holds (user scanning uriage.fr
gets Avène pages, never Uriage's, for FAQ ; for article, brand-bias is
already enforced by the picker's own_brand/competitor_domain filter).

Outcomes
- FAQ new deep page found      → target_url set, source='sitemap_index' or 'auto_suggest'
- Article new media found      → target_url set, source='media_picker'
- Matcher exhausted            → target_url=NULL, source='pending_user'
                                  (the validation UI shows the manual-pick banner)

The job is free (no content_credit debit/refund) because it's a small
single-question call and we want zero friction on iteration. Hard-capped at
REMATCH_MAX_ATTEMPTS_PER_ITEM (10) at the API layer.
"""

import logging
from urllib.parse import urlparse

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _url_to_domain(url: str | None) -> str:
    """Extract bare lowercase domain from a URL (https://www.foo.com/x → foo.com)."""
    if not url:
        return ""
    try:
        netloc = (urlparse(url).netloc or "").lower()
    except Exception:
        return ""
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc.split("/", 1)[0].rstrip(".")


def execute(job_payload: dict, scan_id: str, db: Session) -> dict:
    from models import Scan, ScanContentItem

    item_id = (job_payload or {}).get("item_id")
    if not item_id:
        raise RuntimeError("rematch_target_url: missing item_id in payload")

    item = db.query(ScanContentItem).filter(ScanContentItem.id == item_id).first()
    if not item:
        raise RuntimeError(f"rematch_target_url: item {item_id} not found")

    scan = db.query(Scan).filter(Scan.id == item.scan_id).first()
    if not scan:
        raise RuntimeError(f"rematch_target_url: scan {item.scan_id} not found")

    # ── Route by content_type ────────────────────────────────────────
    # Article items have a completely different discovery model
    # (citations → media partners, not brand-site embeddings).
    if item.content_type == "netlinking_article":
        return _rematch_media_partner(item, scan, db)

    # Reuse the exact target_site resolver materialize uses. We pass `item`
    # so a per-item LEAD override (set via the validation-page star picker)
    # takes priority over the workspace default. Without item context, the
    # rematch would always run on workspace lead even if the user explicitly
    # picked a different brand for THIS opportunity.
    from handlers.materialize_content_items import _resolve_target_site
    target_site, lead_name = _resolve_target_site(scan, db, item=item)
    if not target_site:
        # No primary brand with a domain — same fallback as initial pass.
        # Item is left in pending_user so the manual-pick banner surfaces.
        logger.info(
            f"rematch_target_url: no primary brand domain for client of scan {scan.id} — "
            f"falling back to pending_user (item {item_id})"
        )
        item.target_url = None
        item.target_url_source = "pending_user"
        db.commit()
        return {"matched": False, "reason": "no_primary_brand_domain"}

    # Build the exclusion list : everything previously rejected + the current
    # target_url (which the user is implicitly rejecting by clicking "Find a
    # different page"). De-dup preserves list order.
    rejected = list(item.rejected_target_urls or [])
    if item.target_url and item.target_url not in rejected:
        rejected.append(item.target_url)

    question_text = (item.target_question or "").strip()
    if not question_text:
        raise RuntimeError(f"rematch_target_url: item {item_id} has no target_question")

    # Persist the exclusion list whether or not we find a new match.
    # Accumulating rejections is the durable user signal — Phase D's
    # sitemap matcher uses it as exclude_urls and the legacy matcher
    # uses it via ExcludingFAQPageMatcher.
    item.rejected_target_urls = rejected

    from handlers.materialize_content_items import (
        _resolve_lead_brand, _strip_tracking_params,
    )

    # ── Layer 1 : sitemap-index semantic matcher (Phase D) ────────────
    try:
        from services.sitemap_matcher import (
            SITEMAP_THRESHOLD, find_best_pages, slugify_brand_name,
        )
        from config import settings
        sitemap_enabled = bool(settings.openai_api_key)
    except Exception as exc:
        logger.warning(f"rematch_target_url: sitemap_matcher import failed ({exc})")
        sitemap_enabled = False

    if sitemap_enabled:
        lead_brand = _resolve_lead_brand(scan, db, item=item)
        if lead_brand:
            gamme_slug = (
                slugify_brand_name(lead_brand.name)
                if lead_brand.parent_id else None
            )
            try:
                matches = find_best_pages(
                    question_text=question_text,
                    client_brand_id=str(lead_brand.id),
                    db=db,
                    openai_api_key=settings.openai_api_key,
                    top_k=3,
                    exclude_urls=rejected,
                    gamme_slug=gamme_slug,
                )
            except Exception as exc:
                logger.exception(
                    f"rematch_target_url: sitemap_matcher crashed ({exc}) — "
                    f"falling back to Layer 2"
                )
                matches = []

            if matches and matches[0]["score"] >= SITEMAP_THRESHOLD:
                top1 = matches[0]
                item.target_url = _strip_tracking_params(top1["url"])
                item.target_url_source = "sitemap_index"
                item.target_url_score = float(top1["score"])
                item.target_url_candidates = [
                    {
                        "url": _strip_tracking_params(m["url"]),
                        "title": m.get("title"),
                        "score": float(m["score"]),
                        "inlink_count": int(m.get("inlink_count") or 0),
                    }
                    for m in matches[:3]
                ]
                if top1.get("title"):
                    item.target_page_title = top1["title"]
                logger.info(
                    f"rematch_target_url: item {item_id} → {item.target_url} "
                    f"via sitemap_index score={top1['score']:.3f} "
                    f"(excluded={len(rejected)}, lead={lead_brand.name})"
                )
                db.commit()
                return {
                    "matched": True,
                    "target_url": item.target_url,
                    "source": "sitemap_index",
                    "score": float(top1["score"]),
                    "excluded_count": len(rejected),
                }
            logger.info(
                f"rematch_target_url: sitemap_index top1 below threshold "
                f"({matches[0]['score']:.3f} if matches else 'no_matches') — "
                f"falling back to web_search"
                if matches else
                f"rematch_target_url: sitemap_index returned 0 matches — "
                f"falling back to web_search"
            )

    # ── Layer 2 : ExcludingFAQPageMatcher web_search (legacy) ─────────

    # Install the geo_content_generator stub so faq_page_matcher imports cleanly
    from handlers.generate_faq import _install_geo_stub
    _install_geo_stub()

    try:
        import pandas as pd
        from adapters.page_matcher_excluding import ExcludingFAQPageMatcher
    except Exception as exc:
        logger.exception(f"rematch_target_url: dependency import failed: {exc}")
        raise

    df = pd.DataFrame([{
        "faq_opportunity_id": str(item.id),
        "target_site": target_site,
        "question_text": question_text,
        "source_name": item.topic_name or "",
    }])

    matcher = ExcludingFAQPageMatcher(max_workers=1, exclude_urls=rejected)
    df = matcher.match_pages(df)

    url = (df.iloc[0].get("target_page_url") or "").strip()
    title = (df.iloc[0].get("target_page_title") or "").strip() or None

    if url:
        item.target_url = _strip_tracking_params(url)
        item.target_url_source = "auto_suggest"
        # Clear sitemap-specific fields when falling back to web_search —
        # otherwise stale candidates from a previous sitemap match linger.
        item.target_url_score = None
        item.target_url_candidates = []
        if title:
            item.target_page_title = title
        logger.info(
            f"rematch_target_url: item {item_id} → {item.target_url} "
            f"via web_search (excluded={len(rejected)}, lead={lead_name})"
        )
        db.commit()
        return {
            "matched": True,
            "target_url": item.target_url,
            "source": "auto_suggest",
            "excluded_count": len(rejected),
        }

    # Matcher exhausted — flip to pending_user, the manual-pick banner picks up.
    item.target_url = None
    item.target_url_source = "pending_user"
    item.target_url_score = None
    item.target_url_candidates = []
    logger.info(
        f"rematch_target_url: item {item_id} — no alternative found after excluding "
        f"{len(rejected)} URL(s); pending_user"
    )
    db.commit()
    return {"matched": False, "excluded_count": len(rejected)}


def _rematch_media_partner(item, scan, db: Session) -> dict:
    """Rematch path for netlinking_article items — routed to media_picker.

    Appends current target_url to rejected_target_urls (de-duped), then
    calls media_picker.pick_media_candidates with the rejected DOMAINS
    (not URLs) as exclude set. Returns top-3 fresh candidates.

    Pending_user fallback when :
      - The question has no citations in scan_llm_results
      - All candidates have been previously rejected
      - All remaining candidates are own brand / competitor / institutional
    """
    item_id = str(item.id)

    # 1. Accumulate rejection. Persist URLs (not domains) for audit /
    # symmetry with FAQ path ; convert to domains for the picker exclude set.
    rejected_urls = list(item.rejected_target_urls or [])
    if item.target_url and item.target_url not in rejected_urls:
        rejected_urls.append(item.target_url)
    item.rejected_target_urls = rejected_urls

    rejected_domains: set[str] = set()
    for u in rejected_urls:
        d = _url_to_domain(u)
        if d:
            rejected_domains.add(d)

    question_text = (item.target_question or "").strip()
    if not question_text:
        raise RuntimeError(f"rematch_target_url (article): item {item_id} has no target_question")

    # 2. Call the picker. It handles its own filtering + LinkFinder enrichment.
    try:
        from services.media_picker import pick_media_candidates
    except Exception as exc:
        logger.exception(
            f"rematch_target_url (article): media_picker import failed ({exc}) — "
            f"falling back to pending_user"
        )
        item.target_url = None
        item.target_url_source = "pending_user"
        db.commit()
        return {"matched": False, "reason": "media_picker_import_failed"}

    try:
        candidates = pick_media_candidates(
            scan_id=str(scan.id),
            db=db,
            target_question=question_text,
            top_k=3,
            exclude_domains=rejected_domains,
        )
    except Exception:
        logger.exception(
            f"rematch_target_url (article): media_picker crashed for item {item_id}"
        )
        candidates = []

    if not candidates:
        # Either no citations for this question, or all candidates already
        # rejected, or all filtered as brand/institutional. User can still
        # set a URL manually.
        item.target_url = None
        item.target_url_source = "pending_user"
        item.target_url_score = None
        item.target_url_candidates = []
        logger.info(
            f"rematch_target_url (article): item {item_id} — no alternative media "
            f"after excluding {len(rejected_domains)} domain(s); pending_user"
        )
        db.commit()
        return {
            "matched": False,
            "excluded_count": len(rejected_domains),
            "reason": "no_remaining_media_candidates",
        }

    top1 = candidates[0]
    item.target_url = top1["url"]
    item.target_url_source = "media_picker"
    item.target_url_score = float(top1.get("relevance_score") or 0.0)
    item.target_page_title = top1.get("name") or top1.get("domain")
    item.target_url_candidates = candidates

    logger.info(
        f"rematch_target_url (article): item {item_id} → {item.target_url} "
        f"(citation={top1.get('citation_count')}, DA={top1.get('da')}, "
        f"price=€{top1.get('price_eur')}, excluded={len(rejected_domains)})"
    )
    db.commit()
    return {
        "matched": True,
        "target_url": item.target_url,
        "source": "media_picker",
        "excluded_count": len(rejected_domains),
        "candidates_count": len(candidates),
    }
