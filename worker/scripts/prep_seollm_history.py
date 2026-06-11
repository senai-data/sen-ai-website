"""Local prep step for the seo-llm history import.

Reads the monthly dimensional exports from the seo-llm SharePoint cache,
joins fact_llm_test + fact_citation + fact_brand_mention with the
dimension tables, filters to the brands that exist as sen-ai scans, and
writes one JSONL bundle per (brand, capture-day) :

    out/{source_domain}/{YYYY-MM-DD}.jsonl

Each line is a fully-joined record ready for import_seollm_history.py
(which runs inside the docker container with stdlib only - no pandas).

Run LOCALLY (pandas available) :
    python worker/scripts/prep_seollm_history.py

Config via env :
    SEOLLM_CACHE  default C:/Users/leed/seo-llm/cache/shared_dimensions/_sharepoint_cache
    OUT_DIR       default ./seollm_history_bundle
    BRANDS        comma-separated source_domains, default = the 5 completed sen-ai scans
    EXCLUDE_DAYS  comma-separated YYYY-MM-DD to skip, default 2026-05-19
                  (the sen-ai scans already cover May 19 with their own run)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

CACHE = Path(os.environ.get("SEOLLM_CACHE", "C:/Users/leed/seo-llm/cache/shared_dimensions/_sharepoint_cache"))
OUT = Path(os.environ.get("OUT_DIR", "./seollm_history_bundle"))
BRANDS = os.environ.get(
    "BRANDS",
    "eau-thermale-avene-fr,aderma-fr,ducray-com,klorane-com,renefurterer-com",
).split(",")
EXCLUDE_DAYS = set(os.environ.get("EXCLUDE_DAYS", "2026-05-19").split(","))


def main() -> int:
    # --- dims -------------------------------------------------------------
    dim_q = pd.read_csv(CACHE / "dim_question.csv", low_memory=False)
    dim_p = pd.read_csv(CACHE / "dim_persona.csv", low_memory=False)
    dim_m = pd.read_csv(CACHE / "dim_model.csv")
    dim_s = pd.read_csv(CACHE / "dim_source.csv")

    qmap = (
        dim_q[["question_id", "persona_id", "source_id", "question_text"]]
        .merge(dim_p[["persona_id", "persona_name"]], on="persona_id", how="left")
        .merge(dim_s[["source_id", "source_domain"]], on="source_id", how="left")
    )
    qmap = qmap[qmap.source_domain.isin(BRANDS)]
    brand_qids = set(qmap.question_id)
    print(f"questions mapped: {len(qmap)} across {qmap.source_domain.nunique()} brands")

    model_map = {
        row.model_id: {"provider": row.provider, "model": row.model_name}
        for row in dim_m.itertuples()
    }

    # --- facts (3 monthly files each) --------------------------------------
    tests = []
    for f in sorted(CACHE.glob("fact_llm_test_*.csv")):
        df = pd.read_csv(f, low_memory=False)
        df = df[df.question_id.isin(brand_qids)]
        tests.append(df)
        print(f"  {f.name}: {len(df)} brand rows")
    tests = pd.concat(tests, ignore_index=True)
    tests["day"] = pd.to_datetime(tests.execution_timestamp, errors="coerce").dt.strftime("%Y-%m-%d")
    tests = tests[~tests.day.isin(EXCLUDE_DAYS)]
    print(f"total test rows after day filter: {len(tests)}")

    test_ids = set(tests.test_id)

    cits = []
    for f in sorted(CACHE.glob("fact_citation_*.csv")):
        df = pd.read_csv(f, low_memory=False)
        cits.append(df[df.test_id.isin(test_ids)])
    cits = pd.concat(cits, ignore_index=True)
    print(f"citation rows: {len(cits)}")

    bms = []
    for f in sorted(CACHE.glob("fact_brand_mention_*.csv")):
        df = pd.read_csv(f, low_memory=False)
        bms.append(df[df.test_id.isin(test_ids)])
    bms = pd.concat(bms, ignore_index=True)
    print(f"brand mention rows: {len(bms)}")

    # Index children by test_id for the join loop.
    cit_by_test: dict = {}
    for r in cits.itertuples():
        cit_by_test.setdefault(r.test_id, []).append(r)
    bm_by_test: dict = {}
    for r in bms.itertuples():
        bm_by_test.setdefault(r.test_id, []).append(r)

    q_by_id = {r.question_id: r for r in qmap.itertuples()}

    def _nan_none(v):
        return None if (v is None or (isinstance(v, float) and pd.isna(v))) else v

    def _int(v):
        v = _nan_none(v)
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    def _bool(v):
        v = _nan_none(v)
        if v is None:
            return False
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "vrai")
        return bool(v)

    # --- write bundles ------------------------------------------------------
    counts: dict = {}
    OUT.mkdir(parents=True, exist_ok=True)
    handles: dict = {}
    try:
        for t in tests.itertuples():
            q = q_by_id.get(t.question_id)
            if q is None:
                continue
            mdl = model_map.get(t.model_id, {"provider": "unknown", "model": None})

            citations = [
                {
                    "url": _nan_none(c.citation_url),
                    "domaine": _nan_none(c.citation_domain),
                    "position_dans_reponse": _int(c.citation_position),
                    "contexte": _nan_none(c.citation_context),
                    "est_site_cible": _bool(c.is_target_site),
                    "is_pr_source": _bool(c.is_pr_source),
                    "source_type": ("grounding" if str(_nan_none(c.source_type) or "").startswith("ground") else "text_mention"),
                }
                for c in cit_by_test.get(t.test_id, [])
            ]
            brand_mentions = [
                {
                    "brand_name": _nan_none(b.brand_name),
                    "brand_name_groupby": _nan_none(b.brand_name_groupby),
                    "brand_category": _nan_none(b.brand_category),
                    "position_index": _int(b.position_index),
                    "position_type": _nan_none(b.position_type),
                    "contexte": _nan_none(b.contexte),
                    "sentiment": _nan_none(b.sentiment),
                    "sentiment_justification": _nan_none(b.sentiment_justification),
                    "est_recommandation": _bool(b.est_recommandation),
                    "type_recommandation": _nan_none(b.type_recommandation),
                    "est_marque_cible": _bool(b.est_marque_cible),
                    "nb_mentions": _int(b.nb_mentions),
                }
                for b in bm_by_test.get(t.test_id, [])
            ]

            rec = {
                "day": t.day,
                "execution_timestamp": _nan_none(t.execution_timestamp),
                "persona_name": q.persona_name,
                "question_text": q.question_text,
                "provider": mdl["provider"],
                "model": mdl["model"],
                "response_text": _nan_none(t.response_text),
                "duration_ms": _int(t.response_duration_ms),
                "input_tokens": _int(t.prompt_tokens),
                "output_tokens": _int(t.completion_tokens),
                "target_cited": _bool(t.target_site_cited),
                "target_position": _int(t.target_site_position),
                "total_citations": _int(t.total_sources_count),
                "citations": citations,
                "brand_mentions": brand_mentions,
                "brand_analysis": {
                    "marque_cible_mentionnee": _bool(t.target_brand_mentioned),
                    "nb_marques": _int(t.nb_brands_mentioned),
                    "sentiment_marque_cible": _nan_none(t.target_brand_sentiment),
                    "position_marque_cible": _int(t.target_brand_position),
                    "_import_origin": "seo-llm-history",
                },
            }

            key = (q.source_domain, t.day)
            if key not in handles:
                d = OUT / q.source_domain
                d.mkdir(parents=True, exist_ok=True)
                handles[key] = (d / f"{t.day}.jsonl").open("w", encoding="utf-8")
            handles[key].write(json.dumps(rec, ensure_ascii=False) + "\n")
            counts[key] = counts.get(key, 0) + 1
    finally:
        for h in handles.values():
            h.close()

    print("\n=== bundles written ===")
    for (dom, day), n in sorted(counts.items()):
        print(f"  {dom} / {day} : {n} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
