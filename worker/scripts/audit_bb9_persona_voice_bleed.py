"""BB.9 empirical audit : does the voice subset of the brand brief actually
contaminate persona descriptions when injected into the persona prompt ?

This script picks one real topic from the Avène scan, calls Claude twice :
  - Run A : full brand brief (voice + audience) — simulates pre-BB.9 behavior
  - Run B : audience-only brief — BB.9 active

Both runs use the EXACT SAME prompt template (`adapters/persona_generator.TOPIC_PROMPT`)
and the EXACT SAME keywords. The only variable is `domain_context`. We then
print the 2 persona descriptions side-by-side so a human can judge whether
voice contamination is real.

Run :
  docker compose exec -T worker python scripts/audit_bb9_persona_voice_bleed.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_WORKER_DIR = Path(__file__).resolve().parent.parent
if str(_WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKER_DIR))

from config import settings
from models import (
    Client, ClientBrand, Scan, ScanTopic, ScanKeyword, get_db,
)
from adapters.brief_injector import format_analysis_context
from adapters.persona_generator import generate_for_topic

SCAN_ID = "90604b64-021c-441a-85df-cc0623de95fd"
# Pick a topic from the Avène scan — use the one with the most keywords
# so we have rich keyword input identical for both runs.

NB_PERSONAS = 2  # keep the comparison short + cheap (~$0.01 per run × 2)


async def _run(label: str, context: str, domain: str, topic_name: str,
               keywords: list[dict]) -> dict:
    print(f"\n{'━' * 75}")
    print(f"▶ {label}")
    print(f"{'━' * 75}")
    print(f"Context length : {len(context)} chars / {len(context.split())} words")
    print()
    result = await generate_for_topic(
        domain=domain,
        topic_name=topic_name,
        keywords=keywords,
        nb_personas=NB_PERSONAS,
        anthropic_api_key=settings.anthropic_api_key,
        domain_context=context,
    )
    return result


def _print_persona(p: dict, idx: int):
    print(f"  Persona #{idx} : {p.get('nom', '?')}")
    pd = p.get("profil_demographique", {})
    print(f"    age              : {pd.get('age', '?')}")
    print(f"    profession       : {pd.get('situation_professionnelle', '?')}")
    print(f"    expertise        : {pd.get('niveau_expertise', '?')}")
    intentions = p.get("intentions_recherche", [])
    print(f"    intentions       : {' · '.join(intentions[:3])}")
    pains = p.get("points_douleur", [])
    print(f"    pain points      : {' · '.join(pains[:3])}")
    parcours = (p.get("parcours_type") or "")[:200]
    print(f"    parcours_type    : {parcours}")
    q = (p.get("questions") or [{}])[0]
    print(f"    1st question     : [{q.get('type_question', '?')}] {q.get('question', '?')}")


async def main():
    db = next(get_db())
    scan = db.query(Scan).filter(Scan.id == SCAN_ID).first()
    if not scan:
        print(f"Scan {SCAN_ID} not found")
        return 1
    client = db.query(Client).filter(Client.id == scan.client_id).first()
    focus = db.query(ClientBrand).filter(ClientBrand.id == scan.focus_brand_id).first()
    if not focus or not focus.brief:
        print("Focus brand has no brief — nothing to compare")
        return 1

    # Pick the topic with the most keywords for richer input
    topic_counts = []
    for t in db.query(ScanTopic).filter(ScanTopic.scan_id == SCAN_ID).all():
        kws = db.query(ScanKeyword).filter(ScanKeyword.topic_id == t.id).all()
        if kws:
            topic_counts.append((t, kws))
    if not topic_counts:
        print("No topics with keywords on this scan")
        return 1
    topic_counts.sort(key=lambda x: len(x[1]), reverse=True)
    topic, kws = topic_counts[0]
    keywords = [
        {"keyword": k.keyword, "traffic": k.traffic, "position": k.position}
        for k in kws
    ][:40]  # cap matches persona_generator behavior

    print("=" * 75)
    print("BB.9 EMPIRICAL AUDIT — persona voice-bleed comparison")
    print("=" * 75)
    print(f"Scan      : {scan.name}")
    print(f"Domain    : {scan.domain}")
    print(f"Focus     : {focus.name}")
    print(f"Topic     : {topic.name}")
    print(f"Keywords  : {len(keywords)}")
    print(f"nb_personas : {NB_PERSONAS}")
    print("=" * 75)

    ctx_full = format_analysis_context(
        scan.config, client.apps, focus.brief, audience_only=False,
    )
    ctx_audience = format_analysis_context(
        scan.config, client.apps, focus.brief, audience_only=True,
    )

    result_a = await _run("RUN A — full brief (pre-BB.9 simulation)",
                          ctx_full, scan.domain, topic.name, keywords)
    result_b = await _run("RUN B — audience-only brief (BB.9 active)",
                          ctx_audience, scan.domain, topic.name, keywords)

    print(f"\n{'=' * 75}")
    print(f"SIDE-BY-SIDE PERSONAS")
    print(f"{'=' * 75}\n")
    print(f"### RUN A personas ({len(result_a.get('personas', []))}, "
          f"in={result_a.get('input_tokens')} out={result_a.get('output_tokens')}, "
          f"{result_a.get('duration_ms')}ms)")
    for i, p in enumerate(result_a.get("personas", []), 1):
        _print_persona(p, i)
        print()

    print(f"### RUN B personas ({len(result_b.get('personas', []))}, "
          f"in={result_b.get('input_tokens')} out={result_b.get('output_tokens')}, "
          f"{result_b.get('duration_ms')}ms)")
    for i, p in enumerate(result_b.get("personas", []), 1):
        _print_persona(p, i)
        print()

    # Voice-bleed sniffer : count occurrences of brand voice tokens in each
    # run's persona descriptions (concatenated). A higher count in RUN A
    # would suggest the LLM lifted brand vocabulary into persona text.
    voice_tokens = [
        "expert", "reassuring", "science-led", "scientifically", "clinical",
        "dermatologist", "soothing", "soothe", "soulager", "apaiser",
        "trustworthy", "caring", "gentle", "tolérance", "dermatologique",
    ]

    def _flatten_personas(personas):
        text_parts = []
        for p in personas:
            text_parts.append(p.get("nom", ""))
            text_parts.append(json.dumps(p.get("profil_demographique") or {}))
            text_parts.extend(p.get("intentions_recherche") or [])
            text_parts.extend(p.get("points_douleur") or [])
            text_parts.append(p.get("parcours_type") or "")
            for q in p.get("questions") or []:
                text_parts.append(q.get("intention_cachee") or "")
        return " ".join(text_parts).lower()

    text_a = _flatten_personas(result_a.get("personas", []))
    text_b = _flatten_personas(result_b.get("personas", []))

    print(f"\n{'=' * 75}")
    print(f"VOICE-BLEED SNIFFER — count of brand-voice tokens in persona text")
    print(f"  (intentions_recherche + points_douleur + parcours_type + intention_cachee)")
    print(f"{'=' * 75}")
    print(f"{'token':<25} {'RUN A':>8} {'RUN B':>8} {'Δ':>6}")
    print(f"{'-' * 25} {'-' * 8} {'-' * 8} {'-' * 6}")
    total_a = total_b = 0
    for tok in voice_tokens:
        ca = text_a.count(tok)
        cb = text_b.count(tok)
        total_a += ca
        total_b += cb
        if ca + cb > 0:
            print(f"{tok:<25} {ca:>8} {cb:>8} {cb - ca:>+6}")
    print(f"{'-' * 25} {'-' * 8} {'-' * 8} {'-' * 6}")
    print(f"{'TOTAL voice tokens':<25} {total_a:>8} {total_b:>8} {total_b - total_a:>+6}")
    print()
    print(f"Context tokens used :")
    print(f"  RUN A context : {len(ctx_full)} chars ({len(ctx_full.split())} words)")
    print(f"  RUN B context : {len(ctx_audience)} chars ({len(ctx_audience.split())} words)")
    print(f"  Reduction     : {100 - 100*len(ctx_audience)/len(ctx_full):.0f}%")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
