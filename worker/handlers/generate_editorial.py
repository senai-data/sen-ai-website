"""Handler: generate editorial summary from scan results using Claude.

Claude interprets KPIs and writes marketing-friendly bullet points.
Inspired by seo-llm/scripts/build_interactive_report.py call_claude_editorial().
"""

import asyncio
import json
import logging
from datetime import datetime

import httpx
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

EDITORIAL_PROMPT = """Tu es un expert en visibilité digitale qui écrit pour des marketeurs juniors (zéro jargon SEO).

Voici les résultats d'un AI Scan pour le site **{domain}** :

{domain_context}

## KPIs globaux
- **Brand mention rate** : {citation_rate}% ({target_cited}/{total_tests} réponses IA mentionnent la marque)
- **Position moyenne** quand mentionné : {avg_position}
- **Providers testés** : {providers}

## Par persona
{persona_summary}

## Top concurrents cités par les IA
{competitor_summary}

## Détail des questions où le site EST cité
{cited_details}

## TÂCHE

Génère un rapport en JSON avec :
1. **hook** : titre accrocheur (max 12 mots, langage marketeur)
2. **summary** : 4 bullet points clés (max 20 mots chacun, 1 stat par bullet)
3. **interpretation** : 2-3 phrases d'interprétation globale (qu'est-ce que ça veut dire concrètement ?)
4. **opportunities** : 3 actions concrètes à prendre (basées sur les données)
5. **competitor_insight** : 1 phrase sur le paysage concurrentiel

RÈGLES :
- Langage simple, pas de jargon technique
- Chaque affirmation doit citer un chiffre exact des données
- Ne pas inventer de données
- Ton professionnel mais accessible

Réponds en JSON :
{{
  "hook": "string",
  "summary": ["bullet 1", "bullet 2", "bullet 3", "bullet 4"],
  "interpretation": "string",
  "opportunities": ["action 1", "action 2", "action 3"],
  "competitor_insight": "string"
}}"""


def execute(job_payload: dict, scan_id: str, db: Session) -> dict:
    """Generate editorial summary from scan results."""
    from models import Scan, ScanLLMResult, ScanQuestion, ScanPersona, ScanTopic
    from config import settings

    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan:
        raise RuntimeError("Scan not found")

    results = db.query(ScanLLMResult).filter(ScanLLMResult.scan_id == scan_id).all()
    if not results:
        raise RuntimeError("No scan results")

    # Build context for Claude — uses brand mention rate (not domain citation)
    total = len(results)
    brand_mentioned = sum(1 for r in results if (r.brand_analysis or {}).get("marque_cible_mentionnee"))
    citation_rate = round(brand_mentioned / total * 100, 1)
    positions = [(r.brand_analysis or {}).get("position_marque_cible") for r in results
                 if (r.brand_analysis or {}).get("position_marque_cible")]
    avg_position = round(sum(positions) / len(positions), 1) if positions else None
    providers = list({r.provider for r in results})

    # Persona summary
    personas = db.query(ScanPersona).filter(ScanPersona.scan_id == scan_id).all()
    persona_lines = []
    for p in personas:
        p_results = [r for r in results if any(
            q.persona_id == p.id for q in db.query(ScanQuestion).filter(ScanQuestion.id == r.question_id).all()
        )]
        p_cited = sum(1 for r in p_results if (r.brand_analysis or {}).get("marque_cible_mentionnee"))
        p_rate = round(p_cited / len(p_results) * 100, 1) if p_results else 0
        topic = db.query(ScanTopic).filter(ScanTopic.id == p.topic_id).first()
        persona_lines.append(f"- {p.name} ({topic.name if topic else '?'}) : {p_rate}% mentionné ({p_cited}/{len(p_results)})")

    # Competitor summary
    all_comp = {}
    for r in results:
        if r.competitor_domains:
            for d, c in r.competitor_domains.items():
                all_comp[d] = all_comp.get(d, 0) + c
    comp_lines = [f"- {d} : {c} mentions" for d, c in sorted(all_comp.items(), key=lambda x: -x[1])[:5]]

    # Mentioned details (brand mentioned in response text)
    cited_lines = []
    for r in results:
        if (r.brand_analysis or {}).get("marque_cible_mentionnee"):
            q = db.query(ScanQuestion).filter(ScanQuestion.id == r.question_id).first()
            p = db.query(ScanPersona).filter(ScanPersona.id == q.persona_id).first() if q else None
            sentiment = (r.brand_analysis or {}).get("sentiment_marque_cible", "?")
            cited_lines.append(f"- [{r.provider}] sentiment={sentiment} | {p.name if p else '?'} | {q.question[:80] if q else '?'}")

    from adapters.brief_injector import format_brief_context
    prompt = EDITORIAL_PROMPT.format(
        domain=scan.domain,
        domain_context=format_brief_context(scan.config),
        citation_rate=citation_rate,
        target_cited=brand_mentioned,
        total_tests=total,
        avg_position=avg_position or "N/A",
        providers=", ".join(providers),
        persona_summary="\n".join(persona_lines) or "Aucun",
        competitor_summary="\n".join(comp_lines) or "Aucun",
        cited_details="\n".join(cited_lines) or "Aucune citation",
    )

    # Call Claude
    import time as _time
    _t0 = _time.time()
    editorial = asyncio.run(_call_claude(prompt, settings.anthropic_api_key))
    _dur = int((_time.time() - _t0) * 1000)

    # Log LLM usage
    from adapters.llm_logger import log_llm_usage
    _usage = editorial.pop("_usage", {})
    log_llm_usage(
        db, provider="anthropic", model="claude-sonnet-4-6",
        operation="generate_editorial", duration_ms=_dur,
        scan_id=scan_id, client_id=str(scan.client_id),
        input_tokens=_usage.get("input_tokens", 0),
        output_tokens=_usage.get("output_tokens", 0),
    )

    # Store editorial in scan summary (must reassign for JSONB change detection)
    from sqlalchemy.orm.attributes import flag_modified
    summary = dict(scan.summary or {})
    summary["editorial"] = editorial
    scan.summary = summary
    flag_modified(scan, "summary")
    scan.updated_at = datetime.utcnow()
    db.commit()

    logger.info(f"Editorial generated for {scan.domain}: {editorial.get('hook', '')}")
    return {"editorial": editorial}


async def _call_claude(prompt: str, api_key: str) -> dict:
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 2048,
        "temperature": 0.5,
        "messages": [{"role": "user", "content": prompt}],
    }

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    text = data["content"][0]["text"].strip()
    usage = data.get("usage", {})
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0].strip()

    result = json.loads(text)
    result["_usage"] = usage
    return result
