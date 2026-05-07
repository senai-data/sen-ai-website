"""Heuristic garde-fous applied to LLM-generated questions.

Two checks ported from seo-llm/src/question_generator.py to harden the SaaS
flow against LLM omissions and quirks :

  1. infer_question_type(text) — fallback type_question via FR keyword matching
     when Claude returns missing/invalid values. Avoids dropping otherwise-valid
     questions in Pydantic Literal validation.

  2. validate_question_coherence(personas) — flags two classes of suspicious
     questions and returns warnings (does NOT delete or reject) :
       - off_topic   : question text contains zero of the persona's
                       mots_cles_associes (likely Claude drift)
       - duplicate   : Jaccard similarity >= 0.7 between two questions in the
                       same persona (quasi-doublons that waste scan credits)

Warnings are stored in `scan.summary["warnings"]` (JSONB) and surfaced in the
UI Personas page as an orange badge with expandable list. The user can edit /
toggle the flagged questions BEFORE launching the scan — preserving the
SaaS editability invariant.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

VALID_QUESTION_TYPES: set[str] = {
    "basique", "validation", "comparative", "technique", "urgente",
}

# FR keyword sets for type inference. Order of evaluation in infer_question_type
# matters: most specific (comparative) wins over more generic (technique).
_COMPARATIVE_KEYWORDS = (
    "compar", "versus", " vs ", "différence", "difference", "meilleur",
    "préférer", "preferer", "choix entre", "ou bien", "plutôt", "plutot",
    "avantage", "inconvénient", "inconvenient", "alternative",
)
_URGENTE_KEYWORDS = (
    "urgent", "rapidement", "vite", "immédiat", "immediat", "secours",
    "crise", "grave", "minuit", "dernière minute", "derniere minute",
)
_TECHNIQUE_KEYWORDS = (
    "comment", "pourquoi", "expliquer", "fonctionn", "mécanisme", "mecanisme",
    "processus", "ingrédient", "ingredient", "formul", "composition",
    "actif", "principe",
)
_VALIDATION_KEYWORDS = (
    "est-ce que", "est-il", "peut-on", "dois-je", "faut-il", "recommand",
    "conseill", "avis", "efficace", "marche", "fonctionne", "j'ai entendu",
    "j ai entendu",
)

# Jaccard threshold above which two same-persona questions are flagged as duplicates.
# 0.7 = 70% word overlap. Empirically ported from seo-llm.
_DUPLICATE_JACCARD_THRESHOLD = 0.7

# Words shorter than this are excluded from Jaccard / coherence checks (FR stopwords noise).
_MIN_WORD_LEN = 3


def infer_question_type(question: str) -> str:
    """Infer one of the 5 canonical types from the question text.

    Returns "basique" as fallback when no specific keyword matches.
    Order : comparative > urgente > technique > validation > basique
    (more-specific patterns checked first).
    """
    if not question or not isinstance(question, str):
        return "basique"
    q = question.lower()
    if any(kw in q for kw in _COMPARATIVE_KEYWORDS):
        return "comparative"
    if any(kw in q for kw in _URGENTE_KEYWORDS):
        return "urgente"
    if any(kw in q for kw in _TECHNIQUE_KEYWORDS):
        return "technique"
    if any(kw in q for kw in _VALIDATION_KEYWORDS):
        return "validation"
    return "basique"


def normalize_question_types(personas: list[dict]) -> list[dict]:
    """For each question in each persona, infer type_question if missing/invalid.

    Mutates personas IN PLACE (replaces invalid type_question values with inferred ones)
    AND returns a list of warning dicts describing each inference.

    Called BEFORE Pydantic validation so that no question gets dropped just because
    Claude omitted or misspelled the type field.
    """
    warnings: list[dict] = []
    for p in personas:
        if not isinstance(p, dict):
            continue
        persona_name = (p.get("nom") or "?").strip() or "?"
        for q in p.get("questions") or []:
            if not isinstance(q, dict):
                continue
            current = q.get("type_question")
            current_norm = current.strip().lower() if isinstance(current, str) else ""
            if current_norm in VALID_QUESTION_TYPES:
                continue
            inferred = infer_question_type(q.get("question") or "")
            q["type_question"] = inferred
            warnings.append({
                "type": "type_inferred",
                "persona": persona_name,
                "question": (q.get("question") or "")[:160],
                "original": current if current is not None else "(missing)",
                "inferred": inferred,
            })
    return warnings


def _word_set(text: str) -> set[str]:
    """Lowercase + split + drop short words. Used by Jaccard comparator."""
    if not text:
        return set()
    return {w for w in text.lower().split() if len(w) >= _MIN_WORD_LEN}


def detect_off_topic_questions(persona: dict, questions: list[dict]) -> list[dict]:
    """Flag questions that don't contain any of the persona's mots_cles_associes.

    Heuristic only — the LLM may have produced a perfectly valid question on a
    related-but-broader topic. We just surface it for user review. We DO NOT drop.
    """
    raw_kws = persona.get("mots_cles_associes") or []
    keywords = [(kw or "").strip().lower() for kw in raw_kws if isinstance(kw, str)]
    keywords = [kw for kw in keywords if kw]
    if not keywords:
        return []  # Nothing to compare against
    persona_name = (persona.get("nom") or "?").strip() or "?"
    out: list[dict] = []
    for q in questions or []:
        q_text = q.get("question", "") if isinstance(q, dict) else ""
        if not q_text:
            continue
        q_lower = q_text.lower()
        if not any(kw in q_lower for kw in keywords):
            out.append({
                "type": "off_topic",
                "persona": persona_name,
                "question": q_text[:160],
                "reason": f"none of persona's {len(keywords)} keywords found in question text",
            })
    return out


def detect_duplicate_questions(persona: dict, questions: list[dict],
                               threshold: float = _DUPLICATE_JACCARD_THRESHOLD) -> list[dict]:
    """Flag pairs of same-persona questions whose word-set Jaccard >= threshold.

    Pairs are reported once (i, j) with i < j. The user can then decide to
    rewrite or disable one of the two.
    """
    persona_name = (persona.get("nom") or "?").strip() or "?"
    word_sets = []
    texts = []
    for q in questions or []:
        q_text = q.get("question", "") if isinstance(q, dict) else ""
        word_sets.append(_word_set(q_text))
        texts.append(q_text)
    out: list[dict] = []
    n = len(word_sets)
    for i in range(n):
        if not word_sets[i]:
            continue
        for j in range(i + 1, n):
            if not word_sets[j]:
                continue
            union = word_sets[i] | word_sets[j]
            if not union:
                continue
            inter = word_sets[i] & word_sets[j]
            jaccard = len(inter) / len(union)
            if jaccard >= threshold:
                out.append({
                    "type": "duplicate",
                    "persona": persona_name,
                    "question_a": texts[i][:160],
                    "question_b": texts[j][:160],
                    "jaccard": round(jaccard, 2),
                })
    return out


def validate_question_coherence(personas: list[dict]) -> list[dict]:
    """Run all coherence checks across all personas and return a flat warning list.

    Each persona dict must have at minimum :
      { "nom": str,
        "mots_cles_associes": [str, ...],
        "questions": [{"type_question": str, "question": str}, ...] }

    Returns a flat list of warning dicts ready to persist in scan.summary["warnings"].
    """
    out: list[dict] = []
    for p in personas or []:
        if not isinstance(p, dict):
            continue
        questions = p.get("questions") or []
        out.extend(detect_off_topic_questions(p, questions))
        out.extend(detect_duplicate_questions(p, questions))
    return out


def summarize_warnings(warnings: list[dict]) -> dict[str, int]:
    """Count warnings by type — useful for UI badge labels."""
    counts: dict[str, int] = {}
    for w in warnings or []:
        t = w.get("type") or "unknown"
        counts[t] = counts.get(t, 0) + 1
    return counts
