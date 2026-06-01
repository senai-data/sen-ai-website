"""BB.9 rigorous A/B audit — 5 runs per version, statistical aggregation.

Addresses the small-N limitation of audit_bb9_persona_voice_bleed.py :
Claude is non-deterministic, so a single A/B sample mixes signal (the
context change) with sampling noise. This script runs N=5 per version
on the SAME topic with the SAME keywords, then reports :

  - voice-token counts : mean ± stdev per token across runs
  - smoking-gun phrases (e.g., 'promesses marketing') : count of runs
    in which the phrase appears
  - persona vocabulary diversity (unique tokens in pain_points text)

Cost : 10 × Claude Haiku ~$0.05 = ~$0.50, ~10-15 min wall time.
Budget cap intact ($1/day/client) — checked before each call.

Run :
  docker compose exec -T worker python scripts/audit_bb9_rigorous.py
"""

from __future__ import annotations

import asyncio
import json
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

_WORKER_DIR = Path(__file__).resolve().parent.parent
if str(_WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKER_DIR))

from config import settings
from models import Client, ClientBrand, Scan, ScanTopic, ScanKeyword, get_db
from adapters.brief_injector import format_analysis_context
from adapters.persona_generator import generate_for_topic

SCAN_ID = "90604b64-021c-441a-85df-cc0623de95fd"
N_RUNS = 5
NB_PERSONAS = 2  # 2 personas per run × 5 runs × 2 versions = 20 personas total

# Voice tokens : copy-paste vocabulary lifted from brand brief voice fields.
VOICE_TOKENS = [
    # English voice
    "expert", "reassuring", "science-led", "scientifically", "clinical",
    "dermatologist", "soothing", "trustworthy", "caring", "gentle",
    # French voice / vocabulary natively in the brand brief
    "soulager", "apaiser", "tolérance", "dermatologique", "dermatologue",
    # Marketing-frame words from claims_guidelines
    "marketing", "promesses", "miracle", "instantané", "transformation",
    "luxueux", "agressif",
]

# Smoking-gun phrases : direct lifts from brand brief that should NOT appear
# in persona descriptions (those describe audience, not brand voice).
SMOKING_GUNS = [
    "promesses marketing",
    "scientifically-backed",
    "scientifiquement prouvé",
    "cliniquement prouvé",
    "recommandé par les dermatologues",
    "haute tolérance",
    "transformation rapide",
    "source thermale",
]


def _flatten_personas(personas: list[dict]) -> str:
    """Concatenate all persona text fields for lexical analysis."""
    parts: list[str] = []
    for p in personas:
        parts.append(p.get("nom", ""))
        pd = p.get("profil_demographique") or {}
        parts.append(json.dumps(pd))
        parts.extend(p.get("intentions_recherche") or [])
        parts.extend(p.get("points_douleur") or [])
        parts.append(p.get("parcours_type") or "")
        for q in p.get("questions") or []:
            parts.append(q.get("intention_cachee") or "")
            parts.append(q.get("question") or "")
    return " ".join(parts).lower()


def _count_tokens(text: str, tokens: list[str]) -> dict[str, int]:
    return {t: text.count(t.lower()) for t in tokens}


def _count_phrases(text: str, phrases: list[str]) -> dict[str, bool]:
    return {ph: ph.lower() in text for ph in phrases}


def _vocab_diversity(text: str) -> int:
    """Unique word count in persona text (proxy for description richness)."""
    words = re.findall(r"\b[a-zéèàâêîôûçœ]{3,}\b", text)
    return len(set(words))


async def _run_one(label: str, run_idx: int, context: str,
                   domain: str, topic_name: str, keywords: list[dict]) -> dict:
    print(f"  [{label} #{run_idx + 1}/{N_RUNS}] calling Claude...", end="", flush=True)
    t0 = time.time()
    result = await generate_for_topic(
        domain=domain,
        topic_name=topic_name,
        keywords=keywords,
        nb_personas=NB_PERSONAS,
        anthropic_api_key=settings.anthropic_api_key,
        domain_context=context,
    )
    dt = time.time() - t0
    personas = result.get("personas", [])
    print(f" {dt:.1f}s, {len(personas)} personas, "
          f"in={result.get('input_tokens')} out={result.get('output_tokens')}")
    return result


def _stats(samples: list[int]) -> dict:
    if not samples:
        return {"n": 0, "mean": 0, "stdev": 0, "min": 0, "max": 0}
    return {
        "n": len(samples),
        "mean": round(statistics.mean(samples), 2),
        "stdev": round(statistics.stdev(samples), 2) if len(samples) > 1 else 0,
        "min": min(samples),
        "max": max(samples),
    }


async def main():
    db = next(get_db())
    scan = db.query(Scan).filter(Scan.id == SCAN_ID).first()
    client = db.query(Client).filter(Client.id == scan.client_id).first()
    focus = db.query(ClientBrand).filter(ClientBrand.id == scan.focus_brand_id).first()

    # Pick top topic (same as previous audit for continuity)
    topic_counts = []
    for t in db.query(ScanTopic).filter(ScanTopic.scan_id == SCAN_ID).all():
        kws = db.query(ScanKeyword).filter(ScanKeyword.topic_id == t.id).all()
        if kws:
            topic_counts.append((t, kws))
    topic_counts.sort(key=lambda x: len(x[1]), reverse=True)
    topic, kws = topic_counts[0]
    keywords = [
        {"keyword": k.keyword, "traffic": k.traffic, "position": k.position}
        for k in kws
    ][:40]

    ctx_full = format_analysis_context(scan.config, client.apps, focus.brief,
                                       audience_only=False)
    ctx_audience = format_analysis_context(scan.config, client.apps, focus.brief,
                                           audience_only=True)

    print("=" * 80)
    print(f"BB.9 RIGOROUS A/B AUDIT — N={N_RUNS} runs per version")
    print("=" * 80)
    print(f"Topic         : {topic.name}")
    print(f"Keywords      : {len(keywords)}")
    print(f"nb_personas   : {NB_PERSONAS} per run")
    print(f"Total Claude calls : {N_RUNS * 2}")
    print(f"Context (full)     : {len(ctx_full)} chars")
    print(f"Context (audience) : {len(ctx_audience)} chars")
    print("=" * 80)

    print(f"\n▶ Running {N_RUNS} × RUN A (full brief)")
    runs_a = []
    for i in range(N_RUNS):
        runs_a.append(await _run_one("FULL", i, ctx_full,
                                     scan.domain, topic.name, keywords))

    print(f"\n▶ Running {N_RUNS} × RUN B (audience-only)")
    runs_b = []
    for i in range(N_RUNS):
        runs_b.append(await _run_one("AUDIENCE", i, ctx_audience,
                                     scan.domain, topic.name, keywords))

    # ── Aggregation ──────────────────────────────────────────────────────
    print(f"\n{'=' * 80}")
    print("VOICE-TOKEN DISTRIBUTION (counts in persona text across N runs)")
    print('=' * 80)
    print(f"{'token':<25} {'RUN A mean±sd (min-max)':<32} {'RUN B mean±sd (min-max)':<32}")
    print('-' * 90)

    overall_a = []
    overall_b = []
    for tok in VOICE_TOKENS:
        counts_a = [_count_tokens(_flatten_personas(r.get('personas', [])), [tok])[tok]
                    for r in runs_a]
        counts_b = [_count_tokens(_flatten_personas(r.get('personas', [])), [tok])[tok]
                    for r in runs_b]
        if sum(counts_a) + sum(counts_b) == 0:
            continue
        sa = _stats(counts_a)
        sb = _stats(counts_b)
        overall_a.append(sum(counts_a))
        overall_b.append(sum(counts_b))
        print(f"{tok:<25} "
              f"{sa['mean']:>5.2f}±{sa['stdev']:>4.2f} ({sa['min']}-{sa['max']})  "
              f"{'':<10}"
              f"{sb['mean']:>5.2f}±{sb['stdev']:>4.2f} ({sb['min']}-{sb['max']})")

    total_a_per_run = [
        sum(_count_tokens(_flatten_personas(r.get('personas', [])), VOICE_TOKENS).values())
        for r in runs_a
    ]
    total_b_per_run = [
        sum(_count_tokens(_flatten_personas(r.get('personas', [])), VOICE_TOKENS).values())
        for r in runs_b
    ]
    sa_total = _stats(total_a_per_run)
    sb_total = _stats(total_b_per_run)
    print('-' * 90)
    print(f"{'TOTAL voice tokens / run':<25} "
          f"{sa_total['mean']:>5.2f}±{sa_total['stdev']:>4.2f} ({sa_total['min']}-{sa_total['max']})  "
          f"{'':<10}"
          f"{sb_total['mean']:>5.2f}±{sb_total['stdev']:>4.2f} ({sb_total['min']}-{sb_total['max']})")
    if sa_total['mean'] > 0:
        delta_pct = 100 * (sb_total['mean'] - sa_total['mean']) / sa_total['mean']
        print(f"\n  Reduction in voice-token mean : {delta_pct:+.1f}%")

    # ── Smoking gun phrases ──────────────────────────────────────────────
    print(f"\n{'=' * 80}")
    print("SMOKING-GUN PHRASES (presence in each run, X / N)")
    print('=' * 80)
    print(f"{'phrase':<40} {'RUN A':>10} {'RUN B':>10}")
    print('-' * 62)
    for ph in SMOKING_GUNS:
        present_a = sum(1 for r in runs_a if ph.lower() in _flatten_personas(r.get('personas', [])))
        present_b = sum(1 for r in runs_b if ph.lower() in _flatten_personas(r.get('personas', [])))
        if present_a + present_b == 0:
            continue
        print(f"{ph:<40} {present_a}/{N_RUNS:>6}   {present_b}/{N_RUNS:>6}")

    # ── Vocabulary diversity (unique-word count per run) ─────────────────
    print(f"\n{'=' * 80}")
    print("VOCABULARY DIVERSITY (unique 3+-letter words in persona text per run)")
    print('=' * 80)
    div_a = [_vocab_diversity(_flatten_personas(r.get('personas', []))) for r in runs_a]
    div_b = [_vocab_diversity(_flatten_personas(r.get('personas', []))) for r in runs_b]
    sda = _stats(div_a)
    sdb = _stats(div_b)
    print(f"  RUN A : mean {sda['mean']} ± {sda['stdev']}, range [{sda['min']}–{sda['max']}]")
    print(f"  RUN B : mean {sdb['mean']} ± {sdb['stdev']}, range [{sdb['min']}–{sdb['max']}]")
    if sda['mean'] > 0:
        print(f"  Δ vocabulary : {100*(sdb['mean']-sda['mean'])/sda['mean']:+.1f}%")

    # ── Cost ─────────────────────────────────────────────────────────────
    print(f"\n{'=' * 80}")
    print("COST")
    print('=' * 80)
    tokens_in_a = sum(r.get('input_tokens', 0) for r in runs_a)
    tokens_out_a = sum(r.get('output_tokens', 0) for r in runs_a)
    tokens_in_b = sum(r.get('input_tokens', 0) for r in runs_b)
    tokens_out_b = sum(r.get('output_tokens', 0) for r in runs_b)
    # Haiku 4.5 pricing : $0.80/1M input, $4/1M output (approx)
    cost_a = (tokens_in_a * 0.80 + tokens_out_a * 4.0) / 1_000_000
    cost_b = (tokens_in_b * 0.80 + tokens_out_b * 4.0) / 1_000_000
    print(f"  RUN A : {tokens_in_a} in + {tokens_out_a} out = ${cost_a:.4f}")
    print(f"  RUN B : {tokens_in_b} in + {tokens_out_b} out = ${cost_b:.4f}")
    print(f"  TOTAL : ${cost_a + cost_b:.4f}")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
