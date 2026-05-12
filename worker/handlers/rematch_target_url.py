"""Handler: rerun FAQPageMatcher on a single content item, skipping URLs the
user has already rejected.

Triggered when the user clicks "Find a different page" on the validation
page. The current `target_url` (if any) is appended to the item's
`rejected_target_urls` list, then ExcludingFAQPageMatcher is invoked on
the user's lead primary brand domain — same target_site resolution as
materialize_content_items, so the competitor-scan brand-bias rule still
holds (user scanning uriage.fr gets Avène pages, never Uriage's).

Outcomes
- New deep page found      → target_url set, source='auto_suggest'
- Matcher exhausted        → target_url=NULL, source='pending_user'
                              (the validation UI then shows the manual-pick
                              banner — same fallback as the initial
                              materialize pass.)

The job is free (no content_credit debit/refund) because it's a small
single-question web_search and we want zero friction on iteration.
"""

import logging

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


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

    # Install the geo_content_generator stub so faq_page_matcher imports cleanly
    # (matches the same pattern used in materialize_content_items + generate_faq).
    from handlers.generate_faq import _install_geo_stub
    _install_geo_stub()

    try:
        import pandas as pd
        from adapters.page_matcher_excluding import ExcludingFAQPageMatcher
        from handlers.materialize_content_items import _strip_tracking_params
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

    # Persist the exclusion list whether or not we found a new match —
    # accumulating rejections is the durable user signal Phase D will fold
    # back into the sitemap-index confidence score.
    item.rejected_target_urls = rejected

    if url:
        item.target_url = _strip_tracking_params(url)
        item.target_url_source = "auto_suggest"
        if title:
            item.target_page_title = title
        logger.info(
            f"rematch_target_url: item {item_id} → {item.target_url} "
            f"(excluded={len(rejected)}, lead={lead_name})"
        )
        db.commit()
        return {"matched": True, "target_url": item.target_url, "excluded_count": len(rejected)}

    # Matcher exhausted — flip to pending_user, the manual-pick banner picks up.
    item.target_url = None
    item.target_url_source = "pending_user"
    logger.info(
        f"rematch_target_url: item {item_id} — no alternative found after excluding "
        f"{len(rejected)} URL(s); pending_user"
    )
    db.commit()
    return {"matched": False, "excluded_count": len(rejected)}
