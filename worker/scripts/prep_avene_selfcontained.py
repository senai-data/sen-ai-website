# -*- coding: utf-8 -*-
"""Local prep for the Avene self-contained history re-import (act-scope follow-up).

The original seo-llm history import (import_seollm_history.py) matched results
to the ROOT scan's questions by exact normalized text. Avene is the only brand
where sen-ai REGENERATED personas + questions for 4 of its 7 topics at the May
scan setup (Cicatrisation, Acne/Cleanance, Eczema, Hydratation/Hydrance), so the
historical questions for those topics text-drifted and were dropped - the 4
import children showed only 3 topics. The other 4 brands matched 100%.

This rebuilds the Avene history as SELF-CONTAINED: each child carries its own
topics/personas/questions (the seo-llm originals). For the 3 topics whose text
+ persona names are identical to the ROOT, the aggregated view still merges by
(text, persona_name) so the per-question trend bridges history<->present; the 4
regenerated topics reappear with their own historical trajectory.

Topic mapping source_topic -> sen-ai topic name is data-driven:
  1) matched-derived (a historical question text == a ROOT question text),
  2) accent-normalized name match of source_topic vs ROOT topic names,
  3) residual bijective token-overlap over the still-unassigned ROOT topics.
The final mapping is printed for review.

Run LOCALLY (pandas):
  python worker/scripts/prep_avene_selfcontained.py
Outputs enriched JSONL bundles to ./avene_selfcontained_bundle/<day>.jsonl with
an added "topic" field (the mapped sen-ai topic name) plus question_type /
intent_category from dim_question.
"""
from __future__ import annotations
import json, os, re, unicodedata, collections
from pathlib import Path
import pandas as pd

CACHE = Path(os.environ.get("SEOLLM_CACHE", "C:/Users/leed/seo-llm/cache/shared_dimensions/_sharepoint_cache"))
OUT = Path(os.environ.get("OUT_DIR", "./avene_selfcontained_bundle"))
ROOT_Q_DUMP = os.environ.get("ROOT_Q_DUMP", "C:/Users/leed/sen-ai-website/all_roots_q.txt")
ROOT_ID = "90604b64-021c-441a-85df-cc0623de95fd"
SOURCE_DOMAIN = "eau-thermale-avene-fr"
EXCLUDE_DAYS = set(os.environ.get("EXCLUDE_DAYS", "2026-05-19").split(","))


def norm(s):
    return re.sub(r"\s+", " ", str(s or "").strip()).lower()


def deacc(s):
    return ''.join(c for c in unicodedata.normalize('NFD', str(s)) if unicodedata.category(c) != 'Mn').lower()


STOP = set("de la le les des du un une et a au aux en pour sur quel quelle quels quelles est ce que qui pas plus avec mon ma mes son sa ses je il elle on dans par ou si comment combien".split())


def toks(s):
    return {w for w in re.findall(r"[a-zàâäéèêëïîôöùûüç]+", deacc(s)) if len(w) > 3 and w not in STOP}


def _nan(v):
    return None if (v is None or (isinstance(v, float) and pd.isna(v))) else v


def _int(v):
    v = _nan(v)
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _bool(v):
    v = _nan(v)
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "vrai")
    return bool(v)


def main():
    dim_q = pd.read_csv(CACHE / "dim_question.csv", low_memory=False)
    dim_p = pd.read_csv(CACHE / "dim_persona.csv", low_memory=False)
    dim_s = pd.read_csv(CACHE / "dim_source.csv", low_memory=False)
    dim_m = pd.read_csv(CACHE / "dim_model.csv")

    av = dim_s[dim_s.source_domain == SOURCE_DOMAIN][["source_id", "source_topic"]]
    src_topic = {r.source_id: r.source_topic for r in av.itertuples()}
    q = (dim_q[dim_q.source_id.isin(src_topic)]
         .merge(dim_p[["persona_id", "persona_name"]], on="persona_id", how="left"))
    qmeta = q.drop_duplicates("question_id")

    # ROOT questions + per-topic token bags (from the prod dump, root rows only)
    root_q = {}
    root_topic_toks = collections.defaultdict(set)
    for line in open(ROOT_Q_DUMP, encoding="utf-8"):
        p = line.rstrip("\n").split("|")
        if len(p) >= 3 and p[0] == ROOT_ID:
            root_q[p[1]] = p[2]
            root_topic_toks[p[2]] |= toks(p[1])
    root_topics = set(root_topic_toks)

    # --- build source_topic -> sen-ai topic mapping (data-driven) ---
    by_src = collections.defaultdict(lambda: {"m": collections.Counter(), "toks": set()})
    for r in qmeta.itertuples():
        st = src_topic[r.source_id]
        by_src[st]["toks"] |= toks(r.question_text)
        t = root_q.get(norm(r.question_text))
        if t:
            by_src[st]["m"][t] += 1
    mapping = {}
    used = set()
    for st, info in by_src.items():            # pass1 matched-derived
        if info["m"]:
            t = info["m"].most_common(1)[0][0]
            mapping[st] = (t, "matched")
            used.add(t)
    for st, info in by_src.items():            # pass2 accent-normalized name match
        if st in mapping:
            continue
        base = deacc(re.sub(r"-[a-z]+$", "", st))
        for t in root_topics:
            if t in used:
                continue
            if base in deacc(t) or deacc(t) in base:
                mapping[st] = (t, "name")
                used.add(t)
                break
    for st, info in by_src.items():            # pass3 residual bijective token overlap
        if st in mapping:
            continue
        best, sc = None, -1.0
        for t in root_topics:
            if t in used:
                continue
            tt = root_topic_toks[t]
            j = len(info["toks"] & tt) / max(1, len(info["toks"] | tt)) if tt else 0
            if j > sc:
                sc, best = j, t
        mapping[st] = (best, "token%.2f" % sc)
        used.add(best)

    print("=== source_topic -> sen-ai topic (Avene) ===")
    for st in sorted(by_src):
        print("  %-20s -> %-18r [%s]" % (st, mapping[st][0], mapping[st][1]))
    topic_of = {st: mapping[st][0] for st in mapping}

    model_map = {r.model_id: {"provider": r.provider, "model": r.model_name} for r in dim_m.itertuples()}
    qrow = {r.question_id: r for r in qmeta.itertuples()}

    # facts (Mar + Apr only; May excluded - native scan covers it)
    tests = []
    for f in sorted(CACHE.glob("fact_llm_test_*.csv")):
        df = pd.read_csv(f, low_memory=False)
        tests.append(df[df.question_id.isin(set(qmeta.question_id))])
    tests = pd.concat(tests, ignore_index=True)
    tests["day"] = pd.to_datetime(tests.execution_timestamp, errors="coerce").dt.strftime("%Y-%m-%d")
    tests = tests[~tests.day.isin(EXCLUDE_DAYS)]
    tids = set(tests.test_id)
    cit_by = collections.defaultdict(list)
    bm_by = collections.defaultdict(list)
    for f in sorted(CACHE.glob("fact_citation_*.csv")):
        for c in pd.read_csv(f, low_memory=False).itertuples():
            if c.test_id in tids:
                cit_by[c.test_id].append(c)
    for f in sorted(CACHE.glob("fact_brand_mention_*.csv")):
        for b in pd.read_csv(f, low_memory=False).itertuples():
            if b.test_id in tids:
                bm_by[b.test_id].append(b)

    OUT.mkdir(parents=True, exist_ok=True)
    handles = {}
    counts = collections.Counter()
    topic_cov = collections.defaultdict(set)
    for t in tests.itertuples():
        qq = qrow.get(t.question_id)
        if qq is None:
            continue
        mdl = model_map.get(t.model_id, {"provider": "unknown", "model": None})
        topic = topic_of.get(src_topic.get(qq.source_id))
        rec = {
            "day": t.day, "execution_timestamp": _nan(t.execution_timestamp),
            "topic": topic, "persona_name": qq.persona_name, "question_text": qq.question_text,
            "question_type": _nan(qq.question_type), "intent_category": _nan(qq.intent_category),
            "provider": mdl["provider"], "model": mdl["model"],
            "response_text": _nan(t.response_text), "duration_ms": _int(t.response_duration_ms),
            "input_tokens": _int(t.prompt_tokens), "output_tokens": _int(t.completion_tokens),
            "target_cited": _bool(t.target_site_cited), "target_position": _int(t.target_site_position),
            "total_citations": _int(t.total_sources_count),
            "citations": [{"url": _nan(c.citation_url), "domaine": _nan(c.citation_domain),
                           "position_dans_reponse": _int(c.citation_position), "contexte": _nan(c.citation_context),
                           "est_site_cible": _bool(c.is_target_site), "is_pr_source": _bool(c.is_pr_source),
                           "source_type": ("grounding" if str(_nan(c.source_type) or "").startswith("ground") else "text_mention")}
                          for c in cit_by.get(t.test_id, [])],
            "brand_mentions": [{"brand_name": _nan(b.brand_name), "brand_name_groupby": _nan(b.brand_name_groupby),
                                "brand_category": _nan(b.brand_category), "position_index": _int(b.position_index),
                                "position_type": _nan(b.position_type), "contexte": _nan(b.contexte),
                                "sentiment": _nan(b.sentiment), "sentiment_justification": _nan(b.sentiment_justification),
                                "est_recommandation": _bool(b.est_recommandation), "type_recommandation": _nan(b.type_recommandation),
                                "est_marque_cible": _bool(b.est_marque_cible), "nb_mentions": _int(b.nb_mentions)}
                               for b in bm_by.get(t.test_id, [])],
            "brand_analysis": {"marque_cible_mentionnee": _bool(t.target_brand_mentioned),
                               "nb_marques": _int(t.nb_brands_mentioned), "sentiment_marque_cible": _nan(t.target_brand_sentiment),
                               "position_marque_cible": _int(t.target_brand_position), "_import_origin": "seo-llm-history"},
        }
        if t.day not in handles:
            handles[t.day] = (OUT / ("%s.jsonl" % t.day)).open("w", encoding="utf-8")
        handles[t.day].write(json.dumps(rec, ensure_ascii=False) + "\n")
        counts[t.day] += 1
        topic_cov[t.day].add(topic)
    for h in handles.values():
        h.close()
    print("\n=== bundles ===")
    for day in sorted(counts):
        print("  %s: %d records, %d topics: %s" % (day, counts[day], len(topic_cov[day]), sorted(topic_cov[day])))


if __name__ == "__main__":
    main()
