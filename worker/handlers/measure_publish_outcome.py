"""Handler: measure T+14 LLM-citation lift after publishing on a suggested media.

Closes the suggest-alternative-media learning loop (Phase MR.4 #3). For a
netlinking_article published on a media chosen via /suggest-media
(target_url_source='media_replacement'), 14 days after publication we compare
the user's brand citation position BEFORE vs AFTER publish (per LLM provider),
store the result in `media_publish_outcome`, and — when the brand gained
visibility — boost that media's `llm_citation_decayed` in `media_catalog` so it
ranks higher for future suggestions.

Pre/post split is by `published_at` ; the post-publish data point is produced
by the existing Pilier 7 `refresh_ai_snapshot` T+14 rescan (worker/main.py).
This handler runs AFTER that data exists.

Brand signal source : `ScanLLMResult.brand_analysis` (marque_cible_mentionnee +
position_marque_cible) — reflects the USER's brand regardless of scan_type,
unlike target_cited/target_position which track the scanned domain.

Idempotent : UPSERT on content_item_id ; re-running recomputes + re-stamps
measured_at. The boost is applied once per positive measurement (guarded by
the outcome row's prior measured_at being NULL).
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Boost applied to media_catalog.llm_citation_decayed on a positive T+14
# outcome. Bounded so a single win can't make a media dominate. Newly-cited
# (brand absent before, present after) is the strongest signal.
BOOST_NEWLY_CITED = 3.0
BOOST_RANK_IMPROVED = 2.0
BOOST_MAX = 5.0


def _normalize_domain(raw: str | None) -> str:
    if not raw:
        return ""
    s = str(raw).strip().lower()
    s = re.sub(r"^https?://", "", s)
    if s.startswith("www."):
        s = s[4:]
    s = s.split("/", 1)[0].rstrip(".")
    return s if "." in s else ""


def _brand_signals(row) -> tuple[bool, int | None]:
    """(cited, position) of the USER's brand in one ScanLLMResult.

    Reads brand_analysis.marque_cible_mentionnee + position_marque_cible.
    position None when not cited or not recorded.
    """
    ba = row.brand_analysis or {}
    cited = bool(ba.get("marque_cible_mentionnee"))
    pos = ba.get("position_marque_cible")
    try:
        pos = int(pos) if pos is not None else None
    except (TypeError, ValueError):
        pos = None
    if not cited:
        return False, None
    return True, pos


def execute(job_payload: dict, scan_id: str | None, db: Session) -> dict:
    from models import ScanContentItem, ScanQuestion, ScanLLMResult
    from sqlalchemy import func

    item_id = job_payload.get("item_id")
    if not item_id:
        return {"status": "error", "error": "missing_item_id"}

    item = db.query(ScanContentItem).filter(ScanContentItem.id == item_id).first()
    if not item:
        return {"status": "error", "error": "item_not_found"}
    if not item.published_at:
        return {"status": "skipped", "reason": "not_published"}

    domain = _normalize_domain(item.target_url)
    if not domain:
        return {"status": "skipped", "reason": "no_target_domain"}

    q_text = (item.target_question or "").strip()
    question = (
        db.query(ScanQuestion)
        .filter(
            ScanQuestion.scan_id == item.scan_id,
            func.lower(ScanQuestion.question) == q_text.lower(),
        )
        .first()
    ) if q_text else None
    if not question:
        return {"status": "skipped", "reason": "question_not_found"}

    rows = (
        db.query(ScanLLMResult)
        .filter(ScanLLMResult.question_id == question.id)
        .order_by(ScanLLMResult.created_at.asc())
        .all()
    )

    # N-runs (T1) - the before/after comparison must read ONE analysis row
    # per (scan, provider) : the consensus row (run_index=0, full
    # brand_analysis) when the scan ran N>1, else its run row(s). Raw run
    # rows at N>1 carry no position and would zero out the lift.
    consensus_keys = {
        (str(r.scan_id), r.provider)
        for r in rows if (r.run_index if r.run_index is not None else 1) == 0
    }
    rows = [
        r for r in rows
        if ((r.run_index if r.run_index is not None else 1) == 0)
        or ((str(r.scan_id), r.provider) not in consensus_keys)
    ]

    by_provider: dict[str, list] = {}
    for r in rows:
        by_provider.setdefault(r.provider, []).append(r)

    per_provider: dict[str, dict] = {}
    best_lift = None
    newly_cited = False
    for provider, prows in by_provider.items():
        pre = [r for r in prows if r.created_at and r.created_at <= item.published_at]
        post = [r for r in prows if r.created_at and r.created_at > item.published_at]
        if not pre or not post:
            continue
        baseline, latest = pre[-1], post[-1]
        b_cited, b_pos = _brand_signals(baseline)
        l_cited, l_pos = _brand_signals(latest)
        lift = (b_pos - l_pos) if (b_pos is not None and l_pos is not None) else None
        per_provider[provider] = {
            "baseline_pos": b_pos,
            "baseline_cited": b_cited,
            "latest_pos": l_pos,
            "cited_now": l_cited,
            "lift": lift,
        }
        if l_cited and not b_cited:
            newly_cited = True
        if lift is not None and (best_lift is None or lift > best_lift):
            best_lift = lift

    if not per_provider:
        # No before/after pair yet — leave for a later sweep (don't write a row).
        return {"status": "skipped", "reason": "insufficient_data"}

    # Was this outcome already measured? (idempotency for the boost)
    prior = db.execute(text("""
        SELECT measured_at FROM media_publish_outcome WHERE content_item_id = :iid
    """), {"iid": item_id}).fetchone()
    already_measured = bool(prior and prior[0])

    # UPSERT the outcome row.
    import json
    db.execute(text("""
        INSERT INTO media_publish_outcome
            (content_item_id, domain, published_at, measured_at,
             citation_lift_t14_per_provider)
        VALUES (:iid, :domain, :pub, NOW(), CAST(:payload AS jsonb))
        ON CONFLICT (content_item_id) DO UPDATE SET
            domain = EXCLUDED.domain,
            measured_at = NOW(),
            citation_lift_t14_per_provider = EXCLUDED.citation_lift_t14_per_provider
    """), {
        "iid": item_id,
        "domain": domain,
        "pub": item.published_at,
        "payload": json.dumps(per_provider),
    })

    # Boost the media on a positive outcome — but only once (skip if this
    # outcome was already measured before, to avoid re-boosting on re-runs).
    boosted = 0.0
    positive = newly_cited or (best_lift is not None and best_lift > 0)
    if positive and not already_measured:
        boost = BOOST_NEWLY_CITED if newly_cited else BOOST_RANK_IMPROVED
        boost = min(BOOST_MAX, boost)
        # Apply to every locale row of this domain (we don't know which
        # country/language the publish helped, the signal is domain-level).
        res = db.execute(text("""
            UPDATE media_catalog
               SET llm_citation_decayed = llm_citation_decayed + :boost,
                   updated_at = NOW()
             WHERE domain = :domain
        """), {"boost": boost, "domain": domain})
        boosted = boost if res.rowcount else 0.0

    db.commit()

    result = {
        "status": "ok",
        "domain": domain,
        "providers_measured": len(per_provider),
        "newly_cited": newly_cited,
        "best_lift": best_lift,
        "positive": positive,
        "boost_applied": boosted,
        "already_measured": already_measured,
    }
    logger.info(f"measure_publish_outcome: item={item_id} {result}")
    return result
