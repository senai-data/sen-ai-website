"""Generate personas + questions per topic using Claude (merged, parallelized).

One Claude call per topic → generates N personas + 15 questions each.
Topics are called in parallel via asyncio.gather for speed.
"""

import asyncio
import json
import logging
import time

import httpx

logger = logging.getLogger(__name__)

# 5 question types × 3 each = 15 canonical questions per persona
QUESTION_TYPES = ["basique", "validation", "comparative", "technique", "urgente"]
QUESTIONS_PER_TYPE = 3
QUESTIONS_PER_PERSONA = len(QUESTION_TYPES) * QUESTIONS_PER_TYPE  # 15

TOPIC_PROMPT = """Tu es un expert en SEO et en personas marketing.
Pour le site **{domain}**, à partir des mots-clés du topic "{topic_name}", génère EXACTEMENT {nb_personas} personas visiteurs distincts ET leurs questions de test.

{domain_context}

## Mots-clés du topic (triés par traffic) :
{keywords_text}

## POUR CHAQUE PERSONA, fournis :
1. Profil : prénom français + courte description, âge, profession, expertise
2. segment_principal : COPIER-COLLER ce texte exact → "{topic_name}"
3. Points de douleur, intentions de recherche, mots-clés associés (extraits de la liste ci-dessus)
4. EXACTEMENT {nb_questions} questions de test (= {nb_types} types × {per_type} chacun)

## Types de questions ({per_type} de chaque) :
- **basique** : question naturelle SANS mentionner de marque
- **validation** : "J'ai entendu que..." ou "Est-ce que [affirmation] ?"
- **comparative** : "Quelle est la meilleure façon de..." / "X ou Y ?"
- **technique** : question experte adaptée au niveau du persona
- **urgente** : situation de crise ("Il est minuit et...")

## RÈGLES :
- Chaque persona = profil DISTINCT (âge, situation, expertise différents)
- Questions = angles DISTINCTS (pas de quasi-doublons)
- Nom : "Prénom, courte description" (3-5 mots)
- segment_principal = "{topic_name}" (COPIE EXACTE, obligatoire)
- Mots-clés extraits de la liste, pas inventés

## FORMAT JSON STRICT :
{{
  "personas": [
    {{
      "nom": "Prénom, courte description",
      "segment_principal": "{topic_name}",
      "profil_demographique": {{
        "age": "string",
        "situation_professionnelle": "string",
        "niveau_expertise": "debutant|intermediaire|expert"
      }},
      "intentions_recherche": ["string"],
      "parcours_type": "string",
      "points_douleur": ["string"],
      "mots_cles_associes": ["string"],
      "opportunites": ["string"],
      "questions": [
        {{
          "type_question": "basique|validation|comparative|technique|urgente",
          "question": "string",
          "intention_cachee": "string"
        }}
      ]
    }}
  ]
}}"""


async def generate_for_topic(
    domain: str,
    topic_name: str,
    keywords: list[dict],
    nb_personas: int,
    anthropic_api_key: str,
    domain_context: str = "",
) -> dict:
    """Generate personas + questions for a SINGLE topic. Called in parallel per topic."""
    kw_sorted = sorted(keywords, key=lambda k: k.get("traffic", 0) or 0, reverse=True)
    top_kws = kw_sorted[:40]
    keywords_text = ", ".join(f"{k['keyword']} ({k.get('traffic', '?')})" for k in top_kws)

    nb_questions = QUESTIONS_PER_PERSONA
    prompt = TOPIC_PROMPT.format(
        domain=domain,
        topic_name=topic_name,
        nb_personas=nb_personas,
        keywords_text=keywords_text,
        nb_questions=nb_questions,
        nb_types=len(QUESTION_TYPES),
        per_type=QUESTIONS_PER_TYPE,
        domain_context=domain_context,
    )

    start = time.time()
    result = await _call_claude(prompt, anthropic_api_key)
    duration_ms = int((time.time() - start) * 1000)

    personas = result.get("personas", [])
    logger.info(
        f"Topic '{topic_name}': {len(personas)} personas, "
        f"{sum(len(p.get('questions', [])) for p in personas)} questions in {duration_ms}ms"
    )

    return {
        "topic_name": topic_name,
        "personas": personas,
        "model": result.get("model"),
        "input_tokens": result.get("input_tokens", 0),
        "output_tokens": result.get("output_tokens", 0),
        "duration_ms": duration_ms,
    }


async def generate_all_topics(
    domain: str,
    topics_with_keywords: list[dict],
    nb_personas: int,
    anthropic_api_key: str,
    domain_context: str = "",
) -> dict:
    """Generate personas + questions for ALL topics in parallel.

    Args:
        domain: Target website domain
        topics_with_keywords: [{name, keywords: [{keyword, traffic, position}]}, ...]
        nb_personas: Personas per topic (e.g., 3 for Balanced, 6 for Deep)
        anthropic_api_key: Claude API key

    Returns:
        dict with: topics (list of per-topic results), totals, duration_ms
    """
    start = time.time()
    logger.info(
        f"Generating {nb_personas} personas/topic × {len(topics_with_keywords)} topics "
        f"× {QUESTIONS_PER_PERSONA} questions/persona for {domain} (PARALLEL)"
    )

    # Filter out topics with 0 keywords
    valid_topics = [t for t in topics_with_keywords if t.get("keywords")]

    # Launch all topics in parallel
    tasks = [
        generate_for_topic(
            domain=domain,
            topic_name=t["name"],
            keywords=t["keywords"],
            nb_personas=nb_personas,
            anthropic_api_key=anthropic_api_key,
            domain_context=domain_context,
        )
        for t in valid_topics
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect results, handle errors
    topic_results = []
    total_personas = 0
    total_questions = 0
    total_input_tokens = 0
    total_output_tokens = 0

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"Topic '{valid_topics[i]['name']}' generation failed: {result}")
            topic_results.append({
                "topic_name": valid_topics[i]["name"],
                "personas": [],
                "error": str(result),
            })
        else:
            topic_results.append(result)
            total_personas += len(result.get("personas", []))
            total_questions += sum(len(p.get("questions", [])) for p in result.get("personas", []))
            total_input_tokens += result.get("input_tokens", 0)
            total_output_tokens += result.get("output_tokens", 0)

    duration_ms = int((time.time() - start) * 1000)
    logger.info(
        f"All topics done: {total_personas} personas, {total_questions} questions in {duration_ms}ms"
    )

    return {
        "topics": topic_results,
        "total_personas": total_personas,
        "total_questions": total_questions,
        "input_tokens": total_input_tokens,
        "output_tokens": total_output_tokens,
        "duration_ms": duration_ms,
    }


async def _call_claude(prompt: str, api_key: str, model: str = "claude-haiku-4-5-20251001") -> dict:
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 16384,
        "temperature": 0.5,
        "messages": [{"role": "user", "content": prompt}],
    }

    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    text = data["content"][0]["text"]
    usage = data.get("usage", {})

    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0].strip()

    # Robust JSON extraction — handles Claude's common quirks:
    # 1. Markdown code blocks  2. Preamble text  3. Trailing text after JSON
    import re
    parsed = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r'\{', text)
        if match:
            depth = 0
            for i in range(match.start(), len(text)):
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            parsed = json.loads(text[match.start():i + 1])
                        except json.JSONDecodeError:
                            pass
                        break

    if parsed is None:
        logger.error(f"JSON extraction failed\nRaw ({len(text)} chars): {text[:2000]}")
        raise ValueError(f"Could not extract JSON from Claude response ({len(text)} chars)")

    return {
        **parsed,
        "model": model,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
    }
