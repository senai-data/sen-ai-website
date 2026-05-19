"""Sprint M: per-question composite scores derived from Sprint J judgments
+ Sprint E entity classification + Phase B intent_category.

Three orthogonal scores per ScanLLMResult (= per (question, provider) pair) :

    visibility_score (0-100)  — how present is the client across the response
                                (entity citations weighted by type + position)
    quality_score    (0-100)  — how well is the client presented when present
                                (enveloppement + sentiment + signal hits)
    defensive_score  (0-100)  — for safety-intent questions, NOT being cited
                                = best ; presence with positive sentiment = worst
                                (Phase B `intent_category` drives the flip)

Composite (mean of the 3 when defensive is applicable, mean of the 2 otherwise)
gives a single 0-100 verdict per response. All scores are pure-function over
data already on the rows — no DB writes, computed on read.

Caller pattern :

    from services.composite_scores import compute_scores
    scores = compute_scores(llm_result, question, judgment)
    # → {"visibility": 73, "quality": 80, "defensive": None, "composite": 76}

The judgment + question params can be None (legacy rows, or judge not yet
run) — the function returns None on the affected dimension rather than
guessing. Sprint M UI renders "—" for None.

See project_phase_judge_and_entities.md for the framework rationale.
"""

from __future__ import annotations

from typing import Any


# Entity-type weights for visibility (0-100 scale). Tuned around brand=40
# being the headline ("La Roche-Posay is recommended"); product = 30 because
# a specific product citation still carries the brand association most of
# the time; range = 25; domain (URL cited as source) = 20; expert_source
# is never counted toward the client's visibility — it's external authority.
_VISIBILITY_WEIGHTS: dict[str, int] = {
    "brand": 40,
    "product": 30,
    "range": 25,
    "domain": 20,
    "expert_source": 0,
}

# Position drift penalty. Index 1 = no penalty; each later position -5
# (so 6th-or-beyond entity contributes 0). Reflects that LLMs front-load
# their primary recommendations — being mentioned at index 3 is meaningfully
# less impactful than at index 1.
_POSITION_PENALTY_STEP = 5

# Safety intents where being cited at all is a NEGATIVE signal (= compliance
# risk). Mirrored from worker/services/intent_taxonomy.SAFETY_INTENTS — kept
# in sync via the api side ; if the taxonomy moves, update here too.
_SAFETY_INTENTS = frozenset({
    "safety_warning",
    "side_effects",
    "contre_indication",
    "complaint_sav",
})


def _clamp(v: float, lo: float = 0, hi: float = 100) -> int:
    return max(int(lo), min(int(hi), int(round(v))))


def _compute_visibility(brand_mentions: list[dict]) -> int:
    """Sum of weighted contributions from target entities cited in the response.

    Multiple mentions of the same entity don't compound — _aggregate_entities
    already deduped them and stored `nb_mentions`. The contribution scales
    on entity_type (different axes) and position (front-of-response bonus).
    """
    if not brand_mentions:
        return 0

    total = 0.0
    for m in brand_mentions:
        if not m.get("est_marque_cible"):
            continue
        etype = (m.get("entity_type") or "brand").lower()
        weight = _VISIBILITY_WEIGHTS.get(etype, 0)
        if weight <= 0:
            continue
        # Position penalty — position_index is 1-based.
        pos = m.get("position_index") or 1
        penalty = max(0, (pos - 1) * _POSITION_PENALTY_STEP)
        contribution = max(0, weight - penalty)
        total += contribution
    return _clamp(total)


def _compute_quality(
    brand_mentions: list[dict],
    judgment: dict | None,
) -> int | None:
    """Quality of presentation when the client IS visible.

    Returns None when:
      - no target entity cited (= no quality to score — visibility is the
        relevant signal there, not quality)
      - judgment hasn't been generated yet (legacy rows, judge skipped)

    Returning None lets the UI render "—" rather than a misleading "0/100"
    that would conflate "we're absent" with "we're poorly presented".
    """
    if not judgment:
        return None
    target_mentions = [m for m in (brand_mentions or []) if m.get("est_marque_cible")]
    if not target_mentions:
        return None

    score = 0.0
    # Envelope quality (0-5) × 10 = 0-50 base.
    env = judgment.get("enveloppement_score")
    if env is not None:
        score += float(env) * 10

    # Positive signal hit contributes if Sprint J judge found pattern match.
    if judgment.get("positive_signal_hit"):
        score += 25
    # Negative signal hit subtracts — both can be true simultaneously, the
    # net is what counts.
    if judgment.get("negative_signal_hit"):
        score -= 15

    # Sentiment on the FIRST target mention (rest is in visibility).
    first = target_mentions[0]
    sent = (first.get("sentiment") or "neutre").lower()
    if sent == "positif":
        score += 15
    elif sent == "negatif":
        score -= 10

    # Intent_addressed = the response answered the underlying need, not
    # just the surface question. Independent +10.
    if judgment.get("intent_addressed"):
        score += 10

    return _clamp(score)


def _compute_defensive(
    intent_category: str | None,
    brand_mentions: list[dict],
) -> int | None:
    """Inverted score for safety-intent questions.

    For a "should I stop using retinol" question, having the brand pushed
    as a solution is editorially wrong (Phase B memo). The score is:
      - 100 : no target entity cited (= correct abstention)
      -  70 : cited only as expert_source (educational context)
      -  60 : cited with neutral sentiment (factual contextualization)
      -  30 : cited with negatif sentiment (mentioned as risk — concerning
              for the brand but at least the LLM isn't promoting it)
      -   0 : cited with positif sentiment (worst — brand pushed into a
              safety context the user didn't want it in)

    Returns None for non-safety intents (defensive_score doesn't apply).
    """
    if not intent_category or intent_category not in _SAFETY_INTENTS:
        return None

    targets = [m for m in (brand_mentions or []) if m.get("est_marque_cible")]
    if not targets:
        return 100

    # If every target hit is via expert_source, count as educational — the
    # brand is referenced as authority, not pushed as solution.
    if all((m.get("entity_type") or "").lower() == "expert_source" for m in targets):
        return 70

    # Take the strongest sentiment among target mentions (positif > negatif > neutre).
    sentiments = {(m.get("sentiment") or "neutre").lower() for m in targets}
    if "positif" in sentiments:
        return 0
    if "negatif" in sentiments:
        return 30
    return 60


def compute_scores(
    brand_mentions: list[dict] | None,
    judgment: dict | None,
    intent_category: str | None,
) -> dict[str, int | None]:
    """Compute all three scores + the composite mean.

    Args:
        brand_mentions  : list from ScanLLMResult.brand_mentions JSONB.
                          Each entry is expected to carry the Sprint E
                          fields (entity_type, est_marque_cible) and the
                          legacy fields (sentiment, position_index).
        judgment        : serialized ScanQuestionJudgment row or None.
        intent_category : Phase B classification from ScanQuestion.intent_category.

    Returns a dict with `visibility`, `quality`, `defensive`, `composite`.
    Any field can be None when not computable.
    """
    brand_mentions = brand_mentions or []
    visibility = _compute_visibility(brand_mentions)
    quality = _compute_quality(brand_mentions, judgment)
    defensive = _compute_defensive(intent_category, brand_mentions)

    # Composite = mean of the dimensions that apply. Visibility always
    # contributes ; quality/defensive only when they returned a number.
    components = [visibility]
    if quality is not None:
        components.append(quality)
    if defensive is not None:
        components.append(defensive)
    composite = _clamp(sum(components) / len(components)) if components else 0

    return {
        "visibility": visibility,
        "quality": quality,
        "defensive": defensive,
        "composite": composite,
    }


# ── Aggregations exposed at the scan level ──────────────────────────────


def aggregate_entity_sov(all_brand_mentions_iter) -> dict[str, dict]:
    """SOV (share of voice) by entity_type across a scan.

    For each entity_type, count :
      - total : every aggregated mention seen across all responses
      - targets : subset where est_marque_cible was true
      - sov : targets / total (None when total == 0)

    Args:
        all_brand_mentions_iter : iterable of lists — typically
            [r.brand_mentions for r in results].

    Returns:
        {entity_type: {"total": int, "targets": int, "sov": float|None}}
    """
    counts: dict[str, dict[str, int]] = {}
    for mentions in all_brand_mentions_iter:
        for m in (mentions or []):
            etype = (m.get("entity_type") or "brand").lower()
            slot = counts.setdefault(etype, {"total": 0, "targets": 0})
            # nb_mentions captures repeats inside ONE response ; if absent
            # treat the mention as 1 (legacy rows).
            nb = m.get("nb_mentions") or 1
            slot["total"] += nb
            if m.get("est_marque_cible"):
                slot["targets"] += nb

    out: dict[str, dict] = {}
    for etype, slot in counts.items():
        sov = (slot["targets"] / slot["total"]) if slot["total"] > 0 else None
        out[etype] = {
            "total": slot["total"],
            "targets": slot["targets"],
            "sov": round(sov, 3) if sov is not None else None,
        }
    return out


def aggregate_judgment_funnel(judgments_iter) -> dict[str, int]:
    """4-issues funnel from Sprint J judgments.

    Each judgment row is classified into one of four buckets based on
    (positive_signal_hit, negative_signal_hit) :

        both     — pos AND neg : mixed signal, response is doing two things
        pos_only — pos only    : grille succeeded, no negative pattern hit
        neg_only — neg only    : grille failed, response shows neg pattern
        neither  — neither     : response is neutral / off-grid

    The 4 buckets sum to the total count of judged responses.

    Args:
        judgments_iter : iterable of ScanQuestionJudgment ORM rows OR
            serialized dicts (both work — we use attribute-or-key access).
    """
    out = {"both": 0, "pos_only": 0, "neg_only": 0, "neither": 0}

    def _get(obj: Any, key: str) -> Any:
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    for j in judgments_iter:
        pos = bool(_get(j, "positive_signal_hit"))
        neg = bool(_get(j, "negative_signal_hit"))
        if pos and neg:
            out["both"] += 1
        elif pos:
            out["pos_only"] += 1
        elif neg:
            out["neg_only"] += 1
        else:
            out["neither"] += 1
    return out
