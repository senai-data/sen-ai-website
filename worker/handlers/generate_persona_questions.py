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

logger = logging.getLogger(__name__)

QUESTION_TYPES = ["basique", "validation", "comparative", "technique", "urgente"]
QUESTIONS_PER_TYPE = 3
NB_QUESTIONS = len(QUESTION_TYPES) * QUESTIONS_PER_TYPE  # 15

QUESTIONS_PROMPT = """Tu es un expert en SEO et personas marketing.
Pour le site **{domain}**, génère EXACTEMENT {nb_questions} questions de test pour cette persona :

**Persona** : {persona_name}
**Topic** : {topic_name}

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


async def _call_claude(prompt: str, api_key: str) -> dict:
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
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 4096,
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

    prompt = QUESTIONS_PROMPT.format(
        domain=scan.domain,
        persona_name=persona.name,
        topic_name=topic_name,
        keywords_text=keywords_text,
        nb_questions=NB_QUESTIONS,
        per_type=QUESTIONS_PER_TYPE,
    )

    start = time.time()
    result = asyncio.run(_call_claude(prompt, settings.anthropic_api_key))
    duration_ms = int((time.time() - start) * 1000)

    # Log LLM usage
    from adapters.llm_logger import log_llm_usage
    _usage = result.pop("_usage", {})
    log_llm_usage(
        db, provider="anthropic", model="claude-haiku-4-5-20251001",
        operation="generate_questions", duration_ms=duration_ms,
        input_tokens=_usage.get("input_tokens", 0),
        output_tokens=_usage.get("output_tokens", 0),
        scan_id=scan_id, client_id=str(scan.client_id),
    )

    questions = result.get("questions", [])
    created = 0
    for q in questions:
        q_text = q.get("question", "").strip()
        q_type = q.get("type_question", "basique")
        if not q_text:
            continue
        db.add(ScanQuestion(
            scan_id=scan_id,
            persona_id=persona_id,
            question=q_text,
            type_question=q_type,
            is_active=True,
        ))
        created += 1

    db.commit()
    logger.info(
        f"Generated {created} questions for persona '{persona.name}' "
        f"(scan {scan_id}) in {duration_ms}ms"
    )
    return {"created": created, "duration_ms": duration_ms}
