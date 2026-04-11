"""Generate test questions per persona using Claude.

Adapted from seo-llm/src/config.py QUESTION_GENERATION_PROMPT.
"""

import json
import logging
import time

import httpx

logger = logging.getLogger(__name__)

QUESTION_PROMPT = """Tu as les personas du site **{target_domain}**.
Je veux tester si ce site est spontanément utilisé comme source par les LLMs.

## Personas :
{personas_json}

Pour **CHAQUE persona**, crée EXACTEMENT {nb_questions} questions de test.
Total attendu : {nb_personas} × {nb_questions} = {total_questions} questions.

## Types de questions (varie les types) :
- **basique** : Question naturelle que ce persona taperait, SANS mentionner de site
- **technique** : Adaptée à son niveau d'expertise
- **urgente** : Situation de crise/urgence propre au persona
- **comparative** : "Quelle est la meilleure façon de..."
- **validation** : "Est-ce que [affirmation] ?" ou "J'ai entendu que..."

## Pour chaque question, ajoute :
- **intention_cachee** : ce que je teste vraiment (ex: tester si {target_domain} est cité sur les conseils eczéma)
- **signal_positif** : quoi observer si {target_domain} est valorisé
- **signal_negatif** : quoi observer s'il ne l'est pas

## RÈGLES :
1. Chaque question DOIT porter sur le topic du persona (segment_principal)
2. Utilise les mots_cles_associes du persona comme guide thématique
3. Le profil colore la question mais ne change pas le sujet
4. ÉVITE les quasi-doublons : chaque question = angle DISTINCT
5. La question doit être testable par un LLM (appeler une réponse qui cite des sources)

Réponds UNIQUEMENT en JSON :
{{
  "questions": [
    {{
      "persona_nom": "string",
      "numero_question": 1,
      "type_question": "basique|technique|urgente|comparative|validation",
      "question": "string",
      "intention_cachee": "string",
      "signal_positif": "string",
      "signal_negatif": "string"
    }}
  ]
}}"""


async def generate_questions(target_domain: str, personas: list[dict],
                             nb_questions: int, anthropic_api_key: str) -> dict:
    """
    Generate test questions for each persona.

    Args:
        target_domain: The website being tested
        personas: List of persona dicts (from persona_generator)
        nb_questions: Questions per persona
        anthropic_api_key: Claude API key

    Returns:
        dict with: questions (list), model, duration_ms, tokens
    """
    personas_json = json.dumps(personas, ensure_ascii=False, indent=2)
    nb_personas = len(personas)

    prompt = QUESTION_PROMPT.format(
        target_domain=target_domain,
        personas_json=personas_json,
        nb_questions=nb_questions,
        nb_personas=nb_personas,
        total_questions=nb_personas * nb_questions,
    )

    logger.info(f"Generating {nb_personas}×{nb_questions}={nb_personas * nb_questions} questions "
                f"for {target_domain} (prompt ~{len(prompt)} chars)")
    start = time.time()

    result = await _call_claude(prompt, anthropic_api_key)
    duration_ms = int((time.time() - start) * 1000)

    questions = result["questions"]
    logger.info(f"Generated {len(questions)} questions in {duration_ms}ms")

    return {
        "questions": questions,
        "model": result.get("model"),
        "input_tokens": result.get("input_tokens", 0),
        "output_tokens": result.get("output_tokens", 0),
        "duration_ms": duration_ms,
    }


async def _call_claude(prompt: str, api_key: str, model: str = "claude-sonnet-4-6") -> dict:
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

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}\nRaw: {text[:2000]}")
        raise

    return {
        "questions": parsed.get("questions", []),
        "model": model,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
    }
