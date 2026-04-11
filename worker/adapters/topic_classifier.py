"""Classify URLs into topics using LLM + programmatic path matching.

Step 1: Claude classifies top ~80 URLs (fast, reliable)
Step 2: Remaining URLs matched by URL path similarity (instant, free)
"""

import json
import logging
import time
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

CLASSIFICATION_PROMPT = """Analyse l'arborescence et les mots-clés SEO du site {domain}. Classifie chaque SECTION du site dans un topic thématique SPÉCIFIQUE.

{domain_context}

## Sections du site avec traffic et mots-clés principaux :
{sections_hint}

## TÂCHE

1. Crée des topics SPÉCIFIQUES basés sur les sections du site.
   BIEN : "Eczéma & dermatite atopique", "Acné & imperfections", "Rougeurs & rosacée"
   MAL : "Conditions cutanées" (trop vague, fourre-tout)
2. Assigne chaque SECTION à exactement UN topic.

Réponds UNIQUEMENT en JSON :
{{
  "topics": [
    {{"nom": "Nom spécifique", "description": "1 phrase descriptive"}}
  ],
  "mapping": {{
    "/section/path": "Nom du topic",
    "/autre/section": "Nom du topic"
  }}
}}

## TÂCHE 2 : Détection et classification des marques
Identifie TOUTES les marques commerciales présentes dans les mots-clés et classifie-les.

Le site analysé est : {domain}

Pour chaque marque, détermine :
- "site_brand" : la marque principale du site analysé
- "site_gamme" : une gamme/ligne de produits du site analysé
- "competitor" : une marque concurrente (autre entreprise)
- "topics" : liste des noms de topics (créés en TÂCHE 1) où cette marque est pertinente

CONTRAINTES :
- 5 à 15 topics SPÉCIFIQUES (chaque topic = 1 thématique claire pour définir un persona)
- Le mapping contient CHAQUE section listée ci-dessus
- Sections de navigation/marque pure → "Marque & navigation"
- Chaque marque DOIT avoir au moins 1 topic dans "topics" (le topic où ses mots-clés apparaissent)
- La marque principale (site_brand) apparaît généralement dans TOUS les topics

JSON final :
{{
  "topics": [...],
  "mapping": {{...}},
  "marques_detectees": [
    {{"name": "Avène", "category": "site_brand", "topics": ["Eczéma & dermatite atopique", "Acné & imperfections"]}},
    {{"name": "Cicalfate", "category": "site_gamme", "topics": ["Eczéma & dermatite atopique"]}},
    {{"name": "La Roche-Posay", "category": "competitor", "topics": ["Acné & imperfections", "Rougeurs & rosacée"]}}
  ]
}}"""


def _analyze_url_tree(urls: list[str]) -> dict[str, str]:
    """Analyze URL tree to find optimal section for each URL.

    Adaptive depth: if a node has >30 direct children, it's a container
    (product listing, category page) → use that depth as section.
    Otherwise, go deeper to find meaningful topic sections.

    Returns: {url: section_path}
    """
    # Build tree: count children at each depth
    from collections import Counter

    # Count direct children at each path prefix
    children_at = {}  # prefix → set of next-level segments
    for url in urls:
        path = urlparse(url).path.rstrip("/")
        parts = [p for p in path.split("/") if p]

        for i in range(len(parts)):
            prefix = "/" + "/".join(parts[:i]) if i > 0 else "/"
            child = parts[i]
            if prefix not in children_at:
                children_at[prefix] = set()
            children_at[prefix].add(child)

    # For each URL, find optimal section depth
    CONTAINER_THRESHOLD = 30  # >30 children = container

    url_sections = {}
    for url in urls:
        path = urlparse(url).path.rstrip("/")
        parts = [p for p in path.split("/") if p]

        if not parts:
            url_sections[url] = "/"
            continue

        # Walk down the tree, stop when we hit a container or reach depth 3
        best_depth = 1
        for depth in range(1, min(len(parts) + 1, 4)):
            prefix = "/" + "/".join(parts[:depth])
            n_children = len(children_at.get(prefix, set()))

            if n_children > CONTAINER_THRESHOLD:
                # This level is a container — stop here
                best_depth = depth
                break
            else:
                # This level has few children — it's a meaningful section
                best_depth = depth

        section = "/" + "/".join(parts[:best_depth])
        url_sections[url] = section

    return url_sections


def _build_prompt(domain: str, keywords: list[dict], max_urls: int = 75, domain_context: str = "") -> tuple[str, list[str]]:
    """Build prompt with a MIX of top-traffic + arborescence diversity.

    Strategy:
    1. Group URLs by site section (first 2 path segments)
    2. Take top 2-3 URLs from each section (ensures all branches represented)
    3. Fill remaining slots with top-traffic URLs

    This gives Claude both the full site structure AND the most important pages.
    """
    url_groups = {}
    for kw in keywords:
        url = kw.get("url", "")
        if not url:
            continue
        if url not in url_groups:
            url_groups[url] = {"keywords": [], "total_traffic": 0}
        url_groups[url]["keywords"].append(kw)
        url_groups[url]["total_traffic"] += kw.get("traffic", 0) or 0

    # Step 1: Analyze URL tree to find adaptive sections per branch
    all_urls_list = list(url_groups.keys())
    url_to_section = _analyze_url_tree(all_urls_list)

    sections = {}
    for url, data in url_groups.items():
        section = url_to_section.get(url, "/")
        if section not in sections:
            sections[section] = []
        sections[section].append((url, data))

    # Step 2: Select URLs with diversity + traffic balance
    # Sort sections by total traffic (most important branches first)
    sections_sorted = sorted(sections.items(), key=lambda x: sum(d["total_traffic"] for _, d in x[1]), reverse=True)

    selected = set()

    # Round 1: top 1 URL from each section (up to 50% budget)
    half_budget = max_urls // 2
    for section, urls in sections_sorted:
        if len(selected) >= half_budget:
            break
        best = max(urls, key=lambda x: x[1]["total_traffic"])
        selected.add(best[0])

    # Round 2: fill rest with top-traffic URLs (regardless of section)
    sorted_all = sorted(url_groups.items(), key=lambda x: x[1]["total_traffic"], reverse=True)
    for url, _ in sorted_all:
        if len(selected) >= max_urls:
            break
        selected.add(url)

    # Log sections with URL counts
    section_counts = {s: len(urls) for s, urls in sorted(sections.items(), key=lambda x: -len(x[1]))}
    logger.info(f"URL tree: {len(sections)} sections from {len(url_groups)} URLs. "
                f"Top sections: {dict(list(section_counts.items())[:10])}")
    logger.info(f"Selected {len(selected)} URLs for LLM classification")

    # Build prompt lines sorted by traffic
    selected_with_data = [(url, url_groups[url]) for url in selected]
    selected_with_data.sort(key=lambda x: x[1]["total_traffic"], reverse=True)

    lines = []
    urls_in_prompt = []
    for url, data in selected_with_data:
        top_kws = sorted(data["keywords"], key=lambda k: k.get("traffic", 0) or 0, reverse=True)[:3]
        kw_str = ", ".join(f"{k['keyword']}" for k in top_kws)
        lines.append(f"- {url}  →  {kw_str}")
        urls_in_prompt.append(url)

    # Build sections hint — only sections with >1 URL (single-URL sections matched by path fallback)
    sections_lines = []
    all_section_names = []
    small_sections = 0
    for section, urls in sorted(sections.items(), key=lambda x: -sum(d["total_traffic"] for _, d in x[1])):
        if len(urls) < 2 and len(sections) > 50:
            small_sections += 1
            continue  # Skip micro-sections, they'll be matched by path fallback
        total_traffic = sum(d["total_traffic"] for _, d in urls)
        all_kws = []
        for _, data in urls:
            all_kws.extend(data["keywords"])
        top_kws = sorted(all_kws, key=lambda k: k.get("traffic", 0) or 0, reverse=True)[:5]
        kw_str = ", ".join(k["keyword"] for k in top_kws)
        sections_lines.append(f"- {section} ({len(urls)} pages, {total_traffic} traf) → {kw_str}")
        all_section_names.append(section)

    if small_sections:
        logger.info(f"Filtered out {small_sections} micro-sections (1 URL each)")

    # Safety: if ALL sections were filtered, keep the top ones by traffic
    if not sections_lines and small_sections > 0:
        logger.warning(f"All {small_sections} sections were micro — keeping top 20 by traffic")
        sections_lines = []
        all_section_names = []
        for section, urls in sorted(sections.items(), key=lambda x: -sum(d["total_traffic"] for _, d in x[1]))[:20]:
            total_traffic = sum(d["total_traffic"] for _, d in urls)
            all_kws = []
            for _, data in urls:
                all_kws.extend(data.get("keywords", []))
            top_kws = sorted(all_kws, key=lambda k: k.get("traffic", 0) or 0, reverse=True)[:5]
            kw_str = ", ".join(k["keyword"] for k in top_kws if isinstance(k, dict) and "keyword" in k)
            sections_lines.append(f"- {section} ({len(urls)} pages, {total_traffic} traf) → {kw_str}")
            all_section_names.append(section)

    sections_hint = "\n".join(sections_lines)

    prompt = CLASSIFICATION_PROMPT.format(
        domain=domain,
        domain_context=domain_context,
        sections_hint=sections_hint,
    )
    return prompt, all_section_names


def _match_remaining_urls(url_to_topic: dict, all_urls: list[str],
                          url_to_section: dict) -> dict:
    """Match unclassified URLs to topics using section-based matching.

    URLs in the same section as a classified URL get the same topic.
    Falls back to longest common path prefix.
    """
    # Build section → topic from classified URLs
    section_to_topic = {}
    for url, topic in url_to_topic.items():
        section = url_to_section.get(url, "/")
        if section not in section_to_topic:
            section_to_topic[section] = topic

    # Also build path-based lookup for fallback
    classified_paths = {}
    for url, topic in url_to_topic.items():
        path = urlparse(url).path.rstrip("/")
        classified_paths[path] = topic

    result = dict(url_to_topic)

    for url in all_urls:
        if url in result:
            continue

        # Try section match first (fastest, most accurate)
        section = url_to_section.get(url, "/")
        if section in section_to_topic:
            result[url] = section_to_topic[section]
            continue

        # Fallback: longest common path prefix
        path = urlparse(url).path.rstrip("/")
        best_topic = None
        best_match_len = 0

        for classified_path, topic in classified_paths.items():
            parts_a = path.split("/")
            parts_b = classified_path.split("/")
            common = sum(1 for a, b in zip(parts_a, parts_b) if a == b)

            if common > best_match_len:
                best_match_len = common
                best_topic = topic

        if best_topic and best_match_len >= 2:
            result[url] = best_topic

    return result


async def classify_urls_into_topics(domain: str, keywords: list[dict],
                                    anthropic_api_key: str, domain_context: str = "") -> dict:
    """
    Classify URLs into topics: LLM for top URLs + path matching for the rest.

    Returns:
        dict with: topics, url_to_topic mapping, stats
    """
    # Get all unique URLs and build section mapping
    all_urls = list({kw.get("url", "") for kw in keywords if kw.get("url")})
    url_to_section = _analyze_url_tree(all_urls)

    # Step 1: Build prompt with SECTIONS (not individual URLs)
    prompt, section_names = _build_prompt(domain, keywords, domain_context=domain_context)
    logger.info(f"Classifying {len(section_names)} sections ({len(all_urls)} URLs) for {domain} via Claude")

    start = time.time()
    result = await _call_claude(prompt, anthropic_api_key)
    llm_duration = int((time.time() - start) * 1000)

    topics = result["topics"]
    section_mapping = result.get("mapping", {})  # section_path → topic_name

    # Step 2: Convert section → topic to URL → topic
    url_to_topic = {}
    for url in all_urls:
        section = url_to_section.get(url, "/")
        topic_name = section_mapping.get(section)
        if topic_name:
            url_to_topic[url] = topic_name
        else:
            # Fallback: try parent sections
            path = urlparse(url).path.rstrip("/")
            parts = [p for p in path.split("/") if p]
            for depth in range(len(parts), 0, -1):
                parent = "/" + "/".join(parts[:depth])
                if parent in section_mapping:
                    url_to_topic[url] = section_mapping[parent]
                    break

    assigned = len(url_to_topic)
    logger.info(f"Classification: {len(section_mapping)} sections mapped, "
                f"{assigned}/{len(all_urls)} URLs assigned, {llm_duration}ms")

    # Convert to topics-with-urls format
    topic_urls = {}
    for url, topic_name in url_to_topic.items():
        if topic_name not in topic_urls:
            topic_urls[topic_name] = []
        topic_urls[topic_name].append(url)

    for topic in topics:
        topic["urls"] = topic_urls.get(topic["nom"], [])

    return {
        "topics": topics,
        "url_to_topic": url_to_topic,
        "marques_detectees": result.get("marques_detectees", []),
        "model": result.get("model"),
        "input_tokens": result.get("input_tokens", 0),
        "output_tokens": result.get("output_tokens", 0),
        "duration_ms": llm_duration,
        "provider": "claude",
        "sections_classified": len(section_mapping),
        "urls_assigned": assigned,
        "unassigned": len(all_urls) - assigned,
    }


async def _call_claude(prompt: str, api_key: str, model: str = "claude-haiku-4-5-20251001") -> dict:
    """Call Claude API."""
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 16384,
        "temperature": 0.3,
        "messages": [{"role": "user", "content": prompt}],
    }

    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    text = data["content"][0]["text"]
    usage = data.get("usage", {})

    # Parse JSON — robust extraction handles Claude's common quirks:
    # 1. Markdown code blocks (```json ... ```)
    # 2. Preamble text before the JSON ("Voici le résultat : { ... }")
    # 3. Trailing text after the JSON
    import re
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0].strip()

    parsed = None
    # Try direct parse first
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Fallback: extract the first { ... } block (greedy, handles nested braces)
        match = re.search(r'\{', text)
        if match:
            start = match.start()
            # Find the matching closing brace
            depth = 0
            for i in range(start, len(text)):
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        json_str = text[start:i + 1]
                        try:
                            parsed = json.loads(json_str)
                        except json.JSONDecodeError:
                            pass
                        break

    if parsed is None:
        logger.error(f"JSON extraction failed\nRaw ({len(text)} chars): {text[:2000]}")
        raise ValueError(f"Could not extract JSON from Claude response ({len(text)} chars)")

    topics = parsed.get("topics", [])
    if not topics:
        raise ValueError(f"No topics in response: {text[:500]}")

    return {
        "topics": topics,
        "mapping": parsed.get("mapping", {}),
        "marques_detectees": parsed.get("marques_detectees", []),
        "model": model,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
    }
