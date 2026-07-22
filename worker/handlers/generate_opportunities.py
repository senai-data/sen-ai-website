"""Handler: compute opportunity scores from scan results.

Inspired by seo-llm/src/faq_opportunity.py compute_faq_opportunities().

CRITIQUE: brand absent + competitor present → create FAQ/netlinking
HAUTE: brand cited but behind competitor → improve content
MOYENNE: brand well positioned or no competition → maintain
"""

import logging
from datetime import datetime

from sqlalchemy.orm import Session

from services.intent_taxonomy import SAFETY_INTENTS, is_safety_intent

logger = logging.getLogger(__name__)


def execute(job_payload: dict, scan_id: str, db: Session) -> dict:
    """Score each test result as an opportunity."""
    from models import Scan, ScanLLMResult, ScanQuestion, ScanPersona, ScanTopic, ScanOpportunity

    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan:
        raise RuntimeError("Scan not found")

    # Phase B Tier A — NULL guard (PR-1, audit 2026-05-19).
    # Caller order is normally:
    #   run_llm_tests → classify_question_intent → generate_opportunities
    # If someone re-runs this handler directly (e.g. via
    # scripts/rebuild_opportunities_all.py) on a scan whose questions are
    # still NULL, every question is treated as promotional_fit (legacy
    # behavior per migration 035) — including safety / SAV ones, which
    # then produce critique opps the LLM will later LOW_QUALITY_SKIP.
    # Fail loud so the operator runs classify_question_intent first
    # rather than burning content credits on opportunities that
    # shouldn't exist.
    unclassified = (
        db.query(ScanQuestion)
        .filter(
            ScanQuestion.scan_id == scan_id,
            ScanQuestion.intent_category.is_(None),
        )
        .count()
    )
    if unclassified > 0:
        raise RuntimeError(
            f"generate_opportunities: {unclassified} questions in scan "
            f"{scan_id} have NULL intent_category. Run "
            f"classify_question_intent first (idempotent, ~$0.0005/question)."
        )

    results = db.query(ScanLLMResult).filter(ScanLLMResult.scan_id == scan_id).all()
    if not results:
        return {"opportunities": 0}

    # P4 streak (migration 061) : load the opportunities of the PREVIOUS
    # completed scan of the lineage, keyed by (normalized question text,
    # provider) - NEVER question_id (rescans copy questions under new ids ;
    # imported lineages point at the root's questions). "Previous" = highest
    # run_index below ours among completed lineage scans (imported lineages :
    # the root carries the LAST run_index, so run_index ordering is the
    # chronological one there too).
    # Legacy pre-P4 rows carry provider NULL -> text-only fallback for the one
    # transition rescan (approximate across providers, but better than a wall
    # of false 'New' chips on gaps that in fact persist).
    # Cross model-era matching is a FEATURE : a gap present before AND after a
    # model change is structural - the streak does not reset at P3 boundaries.
    # Defensive try/except : generate_opportunities is in the main post-scan
    # chain (not POST_SCAN_AUDIT_JOB_TYPES), a raise here would cascade to
    # scan.status='failed' - streaks degrade to 'new' instead.
    from handlers.materialize_content_items import _normalize_question

    prev_exact: dict = {}   # (qtext_norm, provider) -> best prev streak
    prev_legacy: dict = {}  # qtext_norm -> best prev streak (pre-P4 rows)
    try:
        root_id = scan.parent_scan_id or scan.id
        cur_idx = scan.run_index or 0
        siblings = db.query(Scan).filter(
            (Scan.id == root_id) | (Scan.parent_scan_id == root_id),
            Scan.status == "completed",
            Scan.id != scan.id,
        ).all()
        prev_scan = max(
            (s for s in siblings if (s.run_index or 0) < cur_idx),
            key=lambda s: ((s.run_index or 0), s.created_at or datetime.min),
            default=None,
        )
        if prev_scan is not None:
            prev_opps = db.query(ScanOpportunity).filter(
                ScanOpportunity.scan_id == prev_scan.id,
            ).all()
            pq_ids = [o.question_id for o in prev_opps if o.question_id]
            prev_qtext = {
                str(row.id): row.question
                for row in db.query(ScanQuestion).filter(ScanQuestion.id.in_(pq_ids)).all()
            } if pq_ids else {}
            for o in prev_opps:
                qtext = _normalize_question(prev_qtext.get(str(o.question_id)))
                if not qtext:
                    continue
                prev_streak = o.streak or 1
                if o.provider:
                    k = (qtext, o.provider)
                    prev_exact[k] = max(prev_exact.get(k, 0), prev_streak)
                else:
                    prev_legacy[qtext] = max(prev_legacy.get(qtext, 0), prev_streak)
            logger.info(
                f"P4 streak: prev scan {prev_scan.id} (run {prev_scan.run_index}) - "
                f"{len(prev_exact)} provider keys + {len(prev_legacy)} legacy keys"
            )
    except Exception:
        logger.exception("P4 streak: failed to index previous scan - all rows will be 'new'")
        prev_exact, prev_legacy = {}, {}

    # Clear previous opportunities
    db.query(ScanOpportunity).filter(ScanOpportunity.scan_id == scan_id).delete()

    personas = {str(p.id): p for p in db.query(ScanPersona).filter(ScanPersona.scan_id == scan_id).all()}
    topics = {str(t.id): t for t in db.query(ScanTopic).filter(ScanTopic.scan_id == scan_id).all()}

    counts = {"critique": 0, "haute": 0, "moyenne": 0}

    # N-runs (T1) - group rows by (question, provider) : run rows
    # (run_index >= 1) carry the per-sample signals, the optional consensus
    # row (run_index = 0) carries the full EntityAnalyzer output. One
    # opportunity per (question, provider), like the legacy 1-row case -
    # at N=1 with no consensus row this degrades to the exact old behavior.
    from collections import defaultdict
    groups = defaultdict(lambda: {"runs": [], "consensus": None})
    for r in results:
        key = (str(r.question_id), r.provider)
        if (r.run_index if r.run_index is not None else 1) == 0:
            groups[key]["consensus"] = r
        else:
            groups[key]["runs"].append(r)

    def _row_mentioned(r) -> bool:
        ba = r.brand_analysis or {}
        return bool(r.target_cited or ba.get("marque_cible_mentionnee", False))

    for (qid, provider), g in groups.items():
        runs = g["runs"]
        if not runs:
            continue
        q = db.query(ScanQuestion).filter(ScanQuestion.id == qid).first()
        if not q:
            continue

        persona = personas.get(str(q.persona_id))
        topic = topics.get(str(persona.topic_id)) if persona and persona.topic_id else None

        # Statistical brand presence : "absent" means absent in >= 80% of
        # runs (mention rate < 0.2). At N=1 this is the old boolean.
        mention_rate = sum(1 for r in runs if _row_mentioned(r)) / len(runs)
        brand_cited = mention_rate >= 0.2

        # Qualitative fields come from the consensus row when present
        # (N > 1), else from the single run row (legacy N=1).
        analysis_row = g["consensus"] or runs[0]
        brand_analysis = analysis_row.brand_analysis or {}
        brand_position = (
            min((r.target_position for r in runs if r.target_position), default=None)
            or brand_analysis.get("position_marque_cible")
        )
        brand_sentiment = brand_analysis.get("sentiment_marque_cible")
        brand_recommended = brand_analysis.get("recommandation_marque_cible", False)

        # Source-domain pressure - union across runs. competitor_domains holds
        # EVERY cited non-target domain (ameli.fr and pubmed land here), so it
        # measures "sources answer this question", NOT competitor brands.
        competitor_domains: dict = {}
        for r in runs:
            for dom, cnt in (r.competitor_domains or {}).items():
                competitor_domains[dom] = competitor_domains.get(dom, 0) + (cnt or 0)
        nb_source_domains = len(competitor_domains)

        # Competitor BRAND pressure - distinct non-target brand mentions from
        # the analysis row (consensus mentions at N>1, run-row at N=1). This
        # is what "competitors cited" means to a marketer ; the legacy scorer
        # counted the domains above and saturated every score at 100.
        competitor_brand_names: set = set()
        best_competitor = None
        best_competitor_pos = None
        best_competitor_domain = None
        for mention in (analysis_row.brand_mentions or []):
            if mention.get("est_marque_cible") or mention.get("contexte_valide") is False:
                continue
            name = mention.get("brand_name_groupby") or mention.get("brand_name")
            if not name:
                continue
            competitor_brand_names.add(name.strip().lower())
            if mention.get("position_index"):
                if best_competitor_pos is None or mention["position_index"] < best_competitor_pos:
                    best_competitor = name
                    best_competitor_pos = mention["position_index"]
        nb_competitor_brands = len(competitor_brand_names)

        # If no brand mentions, fall back to the top cited domain
        if not best_competitor and competitor_domains:
            top_domain = max(competitor_domains, key=competitor_domains.get)
            best_competitor = top_domain
            best_competitor_domain = top_domain

        # Score opportunity
        priority, score = _compute_priority(
            brand_cited=brand_cited,
            brand_position=brand_position,
            nb_competitor_brands=nb_competitor_brands,
            nb_source_domains=nb_source_domains,
            best_competitor_pos=best_competitor_pos,
            mention_rate=mention_rate,
            intent_category=q.intent_category,
        )

        if priority:
            # Phase B Tier A — drop absent-brand opportunities on safety /
            # side-effects / contre-indication / SAV intents. Brand
            # placement reads awkward there and the generator would
            # LOW_QUALITY_SKIP downstream anyway. Guard on brand_cited,
            # not priority : the 2026-07-18 rescore moved domain-only gaps
            # from critique to haute, and those must stay covered (they
            # were dropped when they were critique). Cited-behind-
            # competitor haute keeps flowing — content_update tweaks an
            # existing brand page, no forced placement involved.
            if not brand_cited and is_safety_intent(q.intent_category):
                logger.info(
                    f"Skipped {priority} opportunity for question {q.id} "
                    f"(intent={q.intent_category})"
                )
                continue
            # Determine recommended action by case, not by priority label.
            # Absent-brand gaps split by INTENT, not just "competitor present"
            # (in a competitive vertical a competitor is cited in ~every gap, so
            # that alone routes everything to netlinking - see the 2026-07 audit
            # that flipped 1506/1506 gaps). The real discriminator :
            #   - promotional_fit (commercial query) + a competitor present
            #       -> netlinking : close it with a paid placement on a third
            #         party media that recommends products. The PLACEMENT MEDIA
            #         comes from the catalogue via materialize's media_picker
            #         (LinkFinder price + Babbar authority + LLM citation), NOT
            #         from the cited domain (the AI cites authorities / brand
            #         sites like aad.org, ameli.fr, competitor .fr - never the
            #         placeable media).
            #   - informational_neutral / other -> faq : own-site how-to /
            #         what-is content the AI can cite directly (free).
            # Volume stays bounded by materialize's 30/scan + 8/topic caps
            # (strongest first). Cited-behind rows tweak the existing page.
            if not brand_cited:
                competitor_present = nb_competitor_brands > 0 or bool(best_competitor_domain)
                if q.intent_category == "promotional_fit" and competitor_present:
                    action = "netlinking"
                else:
                    action = "faq"
            elif priority == "haute":
                action = "content_update"
            else:
                action = None

            # P4 streak : exact (text, provider) key first, then the legacy
            # text-only fallback (prev rows without provider).
            qtext_norm = _normalize_question(q.question)
            prev_streak = prev_exact.get((qtext_norm, provider)) or prev_legacy.get(qtext_norm)

            db.add(ScanOpportunity(
                scan_id=scan_id,
                question_id=q.id,
                topic_name=topic.name if topic else None,
                persona_name=persona.name if persona else None,
                provider=provider,
                status="persisting" if prev_streak else "new",
                streak=(prev_streak + 1) if prev_streak else 1,
                brand_cited=brand_cited,
                brand_position=brand_position,
                brand_sentiment=brand_sentiment,
                brand_recommended=brand_recommended,
                best_competitor_name=best_competitor,
                best_competitor_position=best_competitor_pos,
                best_competitor_domain=best_competitor_domain,
                # Competitor BRANDS, not cited domains - the UI renders this
                # as "N competitors cited" and domains made that a lie.
                nb_competitors_cited=nb_competitor_brands,
                priority=priority,
                opportunity_score=score,
                recommended_action=action,
            ))
            counts[priority] += 1

    # Update scan summary with opportunity counts
    from sqlalchemy.orm.attributes import flag_modified
    summary = dict(scan.summary or {})
    summary["opportunities"] = counts
    scan.summary = summary
    flag_modified(scan, "summary")
    scan.updated_at = datetime.utcnow()
    db.commit()

    total = sum(counts.values())

    # Bridge: materialize ScanContentItem rows from the FAQ-eligible opportunities
    # we just wrote, so the Content Kanban gets populated automatically. Runs
    # after this handler completes (FIFO queue), reads ScanOpportunity rows.
    # Skip the enqueue if no opportunities qualify — saves a no-op job.
    if counts.get("critique", 0) + counts.get("haute", 0) > 0:
        from models import Job
        db.add(Job(scan_id=scan_id, job_type="materialize_content_items"))
        db.commit()
        logger.info(f"Enqueued materialize_content_items for scan {scan_id}")

    logger.info(f"Generated {total} opportunities: {counts}")
    return {"total": total, **counts}


def _compute_priority(
    brand_cited,
    brand_position,
    nb_competitor_brands,
    nb_source_domains,
    best_competitor_pos,
    mention_rate,
    intent_category,
):
    """Score an opportunity based on brand vs competitor positioning.

    Recalibrated 2026-07-18. The legacy formula (80 + min(domains*5, 20))
    saturated at critique/100 on nearly every absent question because every
    cited SOURCE domain counted as a competitor - 382 identical rows ranked
    nothing. Changes :
      - critique = a NOMINAL steal only (absent + at least one competitor
        BRAND named instead). Scored 45-100 on brand pressure, leader
        strength, persistence across runs, and intent fit (C.3 downweight
        for informational_neutral - brand fit unproven there).
      - absent with only source domains answering = haute (a content gap
        to fill, not a crisis) scored 40-55.
      - cited-behind-competitor haute and the moyenne cases are unchanged
        except the haute band (40-65) no longer overlaps critique.
    """
    if not brand_cited and nb_competitor_brands > 0:
        score = 55
        score += min(nb_competitor_brands * 4, 16)
        if best_competitor_pos == 1:
            score += 8
        # mention_rate is this (question, provider)'s rate across the N runs :
        # 0.0 = absent on every sample = the most persistent gap.
        score += round((1 - (mention_rate or 0)) * 12)
        if intent_category == "promotional_fit":
            score += 9
        elif intent_category == "informational_neutral":
            score -= 10
        return "critique", max(45, min(score, 100))

    if not brand_cited and nb_source_domains > 0:
        # HAUTE: nobody names a brand but sources answer the question -
        # citable content wins this space.
        return "haute", 40 + min(nb_source_domains * 2, 15)

    if not brand_cited:
        # MOYENNE: nobody cited at all, open space
        return "moyenne", 30

    if brand_cited and best_competitor_pos and brand_position:
        if best_competitor_pos < brand_position:
            # HAUTE: cited but behind competitor
            gap = brand_position - best_competitor_pos
            return "haute", 40 + min(gap * 8, 25)

    if brand_cited and brand_position and brand_position <= 2:
        # Well positioned, not an opportunity
        return None, 0

    if brand_cited:
        # Cited but could be better
        return "moyenne", 20

    return None, 0
