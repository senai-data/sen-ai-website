"""Handler: generate 15 questions for a single custom persona using Claude.

Called when user adds a custom persona via the UI. Generates 5 question types
× 3 each = 15 questions, same balance as the bulk persona generator.
"""

import asyncio
import json
import logging
import time
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

from heuristics import (
    detect_duplicate_questions,
    detect_off_topic_questions,
    normalize_question_types,
)
from schemas import QuestionGenerated, validate_items
from utils import max_tokens_for

logger = logging.getLogger(__name__)

QUESTION_TYPES = ["basique", "validation", "comparative", "technique", "urgente"]
QUESTIONS_PER_TYPE = 3
NB_QUESTIONS = len(QUESTION_TYPES) * QUESTIONS_PER_TYPE  # 15

QUESTIONS_PROMPT = """Tu es un expert en SEO et personas marketing.
Pour le site **{domain}**, génère EXACTEMENT {nb_questions} questions de test pour cette persona :

**Persona** : {persona_name}
**Topic** : {topic_name}
{persona_context}

## Mots-clés du topic (triés par traffic) :
{keywords_text}

## Types de questions ({per_type} de chaque) :
- **basique** : question naturelle SANS mentionner de marque
- **validation** : "J'ai entendu que..." ou "Est-ce que [affirmation] ?"
- **comparative** : "Quelle est la meilleure façon de..." / "X ou Y ?"
- **technique** : question experte adaptée au contexte du persona
- **urgente** : situation de crise ("Il est minuit et...")

## RÈGLES :
- EXACTEMENT {nb_questions} questions ({per_type} par type)
- Questions = angles DISTINCTS (pas de quasi-doublons)
- Formulées comme un vrai utilisateur parlerait à un chatbot AI
- En français

## FORMAT JSON STRICT :
{{
  "questions": [
    {{
      "type_question": "basique|validation|comparative|technique|urgente",
      "question": "string",
      "intention_cachee": "string"
    }}
  ]
}}"""


async def _call_claude(prompt: str, api_key: str, model: str) -> dict:
    """Call Claude Haiku for fast question generation."""
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": max_tokens_for(model, cap=4096),
                "temperature": 0.5,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["content"][0]["text"]

        # Robust JSON extraction (brace-counter approach)
        start = text.find("{")
        if start == -1:
            raise ValueError("No JSON object found in Claude response")
        depth = 0
        end = start
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        parsed = json.loads(text[start:end + 1])
        parsed["_usage"] = data.get("usage", {})
        return parsed


def execute(job_payload: dict, scan_id: str, db: Session) -> dict:
    """Generate 15 questions for a specific persona."""
    from models import Scan, ScanPersona, ScanTopic, ScanKeyword, ScanQuestion
    from config import settings

    persona_id = job_payload.get("persona_id")
    if not persona_id:
        raise RuntimeError("persona_id required in job payload")

    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan:
        raise RuntimeError("Scan not found")

    persona = db.query(ScanPersona).filter(ScanPersona.id == persona_id).first()
    if not persona:
        raise RuntimeError("Persona not found")

    # Get topic + keywords for context
    topic_name = "General"
    keywords_text = scan.domain
    if persona.topic_id:
        topic = db.query(ScanTopic).filter(ScanTopic.id == persona.topic_id).first()
        if topic:
            topic_name = topic.name
            kws = db.query(ScanKeyword).filter(
                ScanKeyword.scan_id == scan_id,
                ScanKeyword.topic_id == topic.id,
            ).order_by(ScanKeyword.traffic.desc().nullslast()).limit(40).all()
            if kws:
                keywords_text = ", ".join(
                    f"{k.keyword} ({k.traffic or '?'})" for k in kws
                )

    # Build optional persona_context block from persona.data — when the user
    # filled the rich Add-persona modal, we feed those fields into the prompt
    # so questions are tailored to that profile rather than generic-topic.
    # Empty fields stay omitted (legacy quick-add behavior preserved).
    pdata = persona.data or {}
    ctx_parts: list[str] = []
    profile = pdata.get("profil_demographique") or {}
    if profile:
        bits = []
        if profile.get("age"): bits.append(f"âge {profile['age']}")
        if profile.get("situation_professionnelle"): bits.append(profile["situation_professionnelle"])
        if profile.get("niveau_expertise"): bits.append(f"niveau {profile['niveau_expertise']}")
        if bits:
            ctx_parts.append(f"**Profil** : {', '.join(bits)}")
    intents = pdata.get("intentions_recherche") or []
    if intents:
        ctx_parts.append("**Intentions de recherche** :\n" + "\n".join(f"- {x}" for x in intents))
    pains = pdata.get("points_douleur") or []
    if pains:
        ctx_parts.append("**Points de douleur** :\n" + "\n".join(f"- {x}" for x in pains))
    user_kws = pdata.get("mots_cles_associes") or []
    if user_kws:
        ctx_parts.append("**Mots-clés associés (fournis par l'utilisateur)** : " + ", ".join(user_kws))
    persona_context = ("\n\n## Profil de cette persona\n" + "\n\n".join(ctx_parts)) if ctx_parts else ""

    prompt = QUESTIONS_PROMPT.format(
        domain=scan.domain,
        persona_name=persona.name,
        topic_name=topic_name,
        persona_context=persona_context,
        keywords_text=keywords_text,
        nb_questions=NB_QUESTIONS,
        per_type=QUESTIONS_PER_TYPE,
    )
    # NW.2 - inject anti-AI-detection humanizer block (compact mode).
    from services.natural_writing_helpers import inject_humanizer
    prompt = inject_humanizer(prompt, mode="compact")

    model = settings.task_models["generate_persona_questions"]
    start = time.time()
    result = asyncio.run(_call_claude(prompt, settings.anthropic_api_key, model=model))
    duration_ms = int((time.time() - start) * 1000)

    # Log LLM usage
    from adapters.llm_logger import log_llm_usage
    _usage = result.pop("_usage", {})
    log_llm_usage(
        db, provider="anthropic", model=model,
        operation="generate_questions", duration_ms=duration_ms,
        input_tokens=_usage.get("input_tokens", 0),
        output_tokens=_usage.get("output_tokens", 0),
        scan_id=scan_id, client_id=str(scan.client_id),
    )

    raw_questions = result.get("questions", [])

    # B.1: infer missing/invalid type_question via heuristic so the item isn't
    # dropped by Pydantic Literal validation. We wrap each question dict in a
    # fake "persona" so normalize_question_types can mutate it in place.
    type_warnings = normalize_question_types([{
        "nom": persona.name,
        "questions": raw_questions,
    }])

    # Pydantic validation: drop malformed items, fail if all invalid
    questions_validated = validate_items(
        raw_questions, QuestionGenerated, "generate_persona_questions.questions"
    )

    created = 0
    for q in questions_validated:
        db.add(ScanQuestion(
            scan_id=scan_id,
            persona_id=persona_id,
            question=q.question,
            type_question=q.type_question,
            is_active=True,
        ))
        created += 1

    # B.2: coherence checks on the new questions (off_topic + duplicates).
    # Run on the validated list combined with the persona context (mots_cles_associes
    # come from persona.data which was set when the persona was created).
    persona_data = persona.data or {}
    persona_dict = {
        "nom": persona.name,
        "segment_principal": topic_name,
        "mots_cles_associes": persona_data.get("mots_cles_associes", []),
        "intentions_recherche": persona_data.get("intentions_recherche", []),
        "points_douleur": persona_data.get("points_douleur", []),
    }
    validated_dicts = [{"question": q.question, "type_question": q.type_question}
                       for q in questions_validated]
    coherence_warnings = (
        detect_off_topic_questions(persona_dict, validated_dicts)
        + detect_duplicate_questions(persona_dict, validated_dicts)
    )
    new_warnings = type_warnings + coherence_warnings

    # Tag warnings with topic name + priority based on topic traffic share
    # (Angle B: weight by business value lost on noisy/duplicate questions).
    if persona.topic_id:
        topic_traffic = sum((k.traffic or 0) for k in db.query(ScanKeyword).filter(
            ScanKeyword.scan_id == scan_id, ScanKeyword.topic_id == persona.topic_id,
        ).all())
        scan_total_traffic = sum((k.traffic or 0) for k in db.query(ScanKeyword).filter(
            ScanKeyword.scan_id == scan_id,
        ).all()) or 1
        share = topic_traffic / scan_total_traffic
        priority = "critical" if share >= 0.20 else "medium" if share >= 0.05 else "low"
    else:
        topic_traffic = 0
        share = 0
        priority = "low"
    for w in new_warnings:
        w["topic"] = topic_name
        w["topic_traffic"] = topic_traffic
        w["topic_share_pct"] = round(share * 100, 1)
        w["priority"] = priority

    # Append (NOT replace) to scan.summary["warnings"] — this handler runs for
    # ONE custom persona; replacing would lose warnings from the bulk generator.
    if new_warnings:
        from sqlalchemy.orm.attributes import flag_modified
        summary = dict(scan.summary or {})
        existing = list(summary.get("warnings", []) or [])
        summary["warnings"] = existing + new_warnings
        scan.summary = summary
        flag_modified(scan, "summary")

    # Increment regen counter in persona.data (success-only — failed runs don't
    # burn the budget). API endpoint caps at MAX via this same field.
    # See feedback_cap_user_triggered_llm_ops.
    from sqlalchemy.orm.attributes import flag_modified as _flag_modified
    pdata = dict(persona.data or {})
    pdata["questions_generations_count"] = int(pdata.get("questions_generations_count") or 0) + 1
    persona.data = pdata
    _flag_modified(persona, "data")

    db.commit()
    logger.info(
        f"Generated {created} questions for persona '{persona.name}' "
        f"(scan {scan_id}) in {duration_ms}ms — {len(new_warnings)} warnings"
    )
    return {"created": created, "warnings_count": len(new_warnings), "duration_ms": duration_ms}
