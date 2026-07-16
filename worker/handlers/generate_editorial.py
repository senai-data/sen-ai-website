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

from schemas import EditorialSummary, validate_object
from utils import max_tokens_for

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

    # N-runs (T1) - rates aggregate over the run rows (run_index >= 1, the
    # actual samples) ; qualitative fields (position) come from the per-pair
    # analysis row : consensus (run_index=0) when present, else the run row.
    # At N=1 without consensus both sets are the same rows = legacy behavior.
    run_rows = [r for r in results if (r.run_index if r.run_index is not None else 1) != 0]
    consensus_by_key = {
        (str(r.question_id), r.provider): r for r in results
        if (r.run_index if r.run_index is not None else 1) == 0
    }
    seen_keys: set = set()
    analysis_rows = []
    for r in run_rows:
        key = (str(r.question_id), r.provider)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        analysis_rows.append(consensus_by_key.get(key) or r)

    # Build context for Claude — uses brand mention rate (not domain citation)
    total = len(run_rows)
    brand_mentioned = sum(1 for r in run_rows if (r.brand_analysis or {}).get("marque_cible_mentionnee"))
    citation_rate = round(brand_mentioned / total * 100, 1) if total else 0.0
    positions = [(r.brand_analysis or {}).get("position_marque_cible") for r in analysis_rows
                 if (r.brand_analysis or {}).get("position_marque_cible")]
    avg_position = round(sum(positions) / len(positions), 1) if positions else None
    providers = list({r.provider for r in run_rows})

    # Position distribution (C.1) — buckets the rank at which the focus brand
    # appears among all brands cited in each AI response. top1 = best (first
    # brand mentioned), top6plus = worst-ranked. Mentioned-but-unranked rolls
    # into top6plus conservatively (rare; happens when BrandAnalyzer detects
    # the mention without an ordering signal).
    position_dist = {"top1": 0, "top2_3": 0, "top4_5": 0, "top6plus": 0, "not_cited": 0}
    # N-runs : ranked over analysis rows (1 per question x provider) - run
    # rows at N>1 carry no position and would all roll into top6plus.
    for r in analysis_rows:
        ba = r.brand_analysis or {}
        if not ba.get("marque_cible_mentionnee"):
            position_dist["not_cited"] += 1
            continue
        pos = ba.get("position_marque_cible")
        if pos is None or pos <= 0:
            position_dist["top6plus"] += 1
        elif pos == 1:
            position_dist["top1"] += 1
        elif pos in (2, 3):
            position_dist["top2_3"] += 1
        elif pos in (4, 5):
            position_dist["top4_5"] += 1
        else:
            position_dist["top6plus"] += 1
    position_dist["total"] = len(analysis_rows)

    # Delta vs parent scan (only if parent persisted a position_distribution).
    # Compare percentages — parent and current may have different total_tests.
    position_dist_delta = None
    if scan.parent_scan_id:
        parent = db.query(Scan).filter(Scan.id == scan.parent_scan_id).first()
        parent_dist = (parent.summary or {}).get("position_distribution") if parent else None
        if parent_dist and parent_dist.get("total"):
            position_dist_delta = {}
            par_total = parent_dist["total"]
            for k in ("top1", "top2_3", "top4_5", "top6plus", "not_cited"):
                curr_pct = (position_dist[k] / total * 100) if total else 0
                par_pct = (parent_dist.get(k, 0) / par_total * 100) if par_total else 0
                position_dist_delta[k] = round(curr_pct - par_pct, 1)

    # Persona summary
    personas = db.query(ScanPersona).filter(ScanPersona.scan_id == scan_id).all()

    # P0 perf : batch the question / persona / topic lookups (was one
    # ScanQuestion query per row PER PERSONA below, plus one per cited row).
    # Lookup by id, never scan_id : imported lineages point results at the
    # ROOT scan's questions (and those questions at the root's personas).
    qids = {r.question_id for r in run_rows} | {r.question_id for r in analysis_rows}
    questions_by_id = {
        q.id: q for q in db.query(ScanQuestion).filter(ScanQuestion.id.in_(qids)).all()
    } if qids else {}
    personas_by_id = {p.id: p for p in personas}
    missing_pids = {q.persona_id for q in questions_by_id.values()
                    if q.persona_id and q.persona_id not in personas_by_id}
    if missing_pids:
        for p in db.query(ScanPersona).filter(ScanPersona.id.in_(missing_pids)).all():
            personas_by_id[p.id] = p
    topic_ids = {p.topic_id for p in personas if p.topic_id}
    topics_by_id = {
        t.id: t for t in db.query(ScanTopic).filter(ScanTopic.id.in_(topic_ids)).all()
    } if topic_ids else {}

    rows_by_persona = {}
    for r in run_rows:
        q = questions_by_id.get(r.question_id)
        if q:
            rows_by_persona.setdefault(q.persona_id, []).append(r)

    persona_lines = []
    for p in personas:
        p_results = rows_by_persona.get(p.id, [])
        p_cited = sum(1 for r in p_results if (r.brand_analysis or {}).get("marque_cible_mentionnee"))
        p_rate = round(p_cited / len(p_results) * 100, 1) if p_results else 0
        topic = topics_by_id.get(p.topic_id) if p.topic_id else None
        persona_lines.append(f"- {p.name} ({topic.name if topic else '?'}) : {p_rate}% mentionné ({p_cited}/{len(p_results)})")

    # Competitor summary (run rows only - consensus rows carry {} anyway ;
    # counts are "across all runs", uniform x N so the top-5 ranking holds)
    all_comp = {}
    for r in run_rows:
        if r.competitor_domains:
            for d, c in r.competitor_domains.items():
                all_comp[d] = all_comp.get(d, 0) + c
    comp_lines = [f"- {d} : {c} mentions" for d, c in sorted(all_comp.items(), key=lambda x: -x[1])[:5]]

    # Mentioned details (brand mentioned in response text). Analysis rows :
    # at N>1 the run rows have no sentiment and would list N duplicates.
    cited_lines = []
    for r in analysis_rows:
        if (r.brand_analysis or {}).get("marque_cible_mentionnee"):
            q = questions_by_id.get(r.question_id)
            p = personas_by_id.get(q.persona_id) if q else None
            sentiment = (r.brand_analysis or {}).get("sentiment_marque_cible", "?")
            cited_lines.append(f"- [{r.provider}] sentiment={sentiment} | {p.name if p else '?'} | {q.question[:80] if q else '?'}")

    from adapters.brief_injector import format_analysis_context
    from models import Client as _Client
    _client = db.query(_Client).filter(_Client.id == scan.client_id).first()
    prompt = EDITORIAL_PROMPT.format(
        domain=scan.domain,
        domain_context=format_analysis_context(scan.config, _client.apps if _client else None),
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
    model = settings.task_models["generate_editorial"]
    from services.byok import resolve_anthropic_key
    anthropic_key, key_source = resolve_anthropic_key(db, scan.client_id)
    _t0 = _time.time()
    editorial = asyncio.run(_call_claude(prompt, anthropic_key, model=model))
    _dur = int((_time.time() - _t0) * 1000)

    # Log LLM usage
    from adapters.llm_logger import log_llm_usage
    _usage = editorial.pop("_usage", {})
    log_llm_usage(
        db, provider="anthropic", model=model,
        operation="generate_editorial", duration_ms=_dur,
        scan_id=scan_id, client_id=str(scan.client_id),
        input_tokens=_usage.get("input_tokens", 0),
        output_tokens=_usage.get("output_tokens", 0),
        key_source=key_source,
    )

    # Pydantic validation on editorial structure
    editorial = validate_object(
        editorial, EditorialSummary, "generate_editorial"
    ).model_dump()

    # Store editorial + position distribution in scan summary
    # (must reassign for JSONB change detection)
    from sqlalchemy.orm.attributes import flag_modified
    summary = dict(scan.summary or {})
    summary["editorial"] = editorial
    summary["position_distribution"] = position_dist
    if position_dist_delta is not None:
        summary["position_distribution_delta"] = position_dist_delta
    scan.summary = summary
    flag_modified(scan, "summary")
    scan.updated_at = datetime.utcnow()
    db.commit()

    logger.info(f"Editorial generated for {scan.domain}: {editorial.get('hook', '')}")
    return {"editorial": editorial}


async def _call_claude(prompt: str, api_key: str, model: str) -> dict:
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": max_tokens_for(model, cap=2048),
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
