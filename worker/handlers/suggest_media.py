"""Handler: run media_replacement.suggest() for one content item.

Reads :
- ScanContentItem (by id from payload) + its joined Scan + ScanQuestion
- media_catalog, scan_llm_results, client_brands, trust_sources, media_feedback

Writes :
- Nothing. Result is returned via Job.result (the API polls it).

Triggered :
- API POST /content-items/{id}/suggest-media enqueues this job.

Job payload :
    {
      "item_id": str (required),
      "strategy": "match_competitor" | "avoid_competitor",
      "price_max": float | null,
      "require_price": bool,
      "exclude_domains": list[str],
      "top_k": int,
    }

Cost : Zero LLM tokens (DB-only Sprint 2). Sprint 3 will add an LLM web_search
fallback path with credit debit + assert_within_budget.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from services.media_replacement import IntentNotEligibleError, suggest

logger = logging.getLogger(__name__)


def execute(job_payload: dict, scan_id: str | None, db: Session) -> dict:
    from models import ScanContentItem

    item_id = job_payload.get("item_id")
    if not item_id:
        return {"status": "error", "error": "missing_item_id"}

    item = db.query(ScanContentItem).filter(ScanContentItem.id == item_id).first()
    if not item:
        return {"status": "error", "error": "item_not_found"}

    try:
        result = suggest(
            db,
            content_item=item,
            strategy=job_payload.get("strategy") or "match_competitor",
            price_max=job_payload.get("price_max"),
            require_price=bool(job_payload.get("require_price", False)),
            exclude_domains=set(job_payload.get("exclude_domains") or []),
            top_k=int(job_payload.get("top_k") or 5),
        )
    except IntentNotEligibleError as e:
        return {
            "status": "intent_not_eligible",
            "intent_category": e.intent_category,
            "message": str(e),
        }
    except Exception as exc:
        logger.exception(f"suggest_media: unexpected error for item {item_id}")
        return {"status": "error", "error": str(exc)}

    logger.info(
        f"suggest_media: item={item_id} → {len(result.get('suggestions', []))} suggestions "
        f"(diagnostics={result.get('diagnostics')})"
    )
    return {"status": "ok", **result}
