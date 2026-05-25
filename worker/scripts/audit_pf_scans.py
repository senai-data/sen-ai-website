"""Audit the imported Pierre Fabre brand scans (Avène, Klorane, A-Derma, Ducray).

Flags: seo-llm import integrity (persona/question counts), scan completion, and —
critically — whether the brand-mention metrics are REAL or a Gemini-failure
artifact (mention_rate==0 with results present = suspect, re-scan needed).

Run:
    docker exec senai-worker python /tmp/audit_pf_scans.py
"""
import sys
sys.path.insert(0, '/app')
from sqlalchemy import func
from models import (SessionLocal, Scan, ScanTopic, ScanPersona, ScanQuestion,
                    ScanLLMResult as R)

PF_CLIENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
BRAND_DOMAINS = ["avene", "klorane", "aderma", "ducray"]

db = SessionLocal()

scans = (
    db.query(Scan)
    .filter(Scan.client_id == PF_CLIENT)
    .order_by(Scan.domain, Scan.run_index)
    .all()
)
matched = [s for s in scans if any(b in (s.domain or "").lower() for b in BRAND_DOMAINS)]

print(f"PF scans total={len(scans)}, matching 4 brands={len(matched)}\n")

for s in matched:
    sid = str(s.id)
    cfg = s.config or {}
    n_topics = db.query(ScanTopic).filter(ScanTopic.scan_id == sid).count()
    n_topics_act = db.query(ScanTopic).filter(ScanTopic.scan_id == sid, ScanTopic.is_active == True).count()
    n_pers = db.query(ScanPersona).filter(ScanPersona.scan_id == sid).count()
    # seo-llm origin count via JSONB
    from sqlalchemy import text
    n_seollm = db.execute(text(
        "SELECT COUNT(*) FROM scan_personas WHERE scan_id=:s AND data->'_source'->>'origin'='seo-llm'"
    ), {"s": sid}).scalar()
    n_q = db.query(ScanQuestion).filter(ScanQuestion.scan_id == sid).count()
    n_res = db.query(R).filter(R.scan_id == sid).count()
    per_prov = dict(db.query(R.provider, func.count()).filter(R.scan_id == sid).group_by(R.provider).all())
    mentioned = db.execute(text(
        "SELECT COUNT(*) FROM scan_llm_results WHERE scan_id=:s AND brand_analysis->>'marque_cible_mentionnee'='true'"
    ), {"s": sid}).scalar()
    cited = db.query(R).filter(R.scan_id == sid, R.target_cited == True).count()
    rate = (mentioned / n_res * 100) if n_res else 0
    cite_rate = (cited / n_res * 100) if n_res else 0

    # Health verdict
    if n_res == 0:
        verdict = "NO RESULTS (not scanned)"
    elif rate == 0 and n_res > 0:
        verdict = "🔴 SUSPECT — 0% mentions w/ results (likely Gemini-failure analysis)"
    elif "gemini" not in per_prov and n_res > 0:
        verdict = "🟠 OpenAI-only (Gemini provider missing)"
    else:
        verdict = "🟢 OK"

    print(f"=== {s.domain}  (run {s.run_index}, {s.status}) ===")
    print(f"  scan_id: {sid}")
    print(f"  import: origin={cfg.get('import_origin')} brand={cfg.get('import_brand')} ts={cfg.get('import_timestamp','')[:19]}")
    print(f"  topics: {n_topics_act} active / {n_topics} total")
    print(f"  personas: {n_pers} ({n_seollm} seo-llm)   questions: {n_q}")
    print(f"  results: {n_res}  per-provider={per_prov}")
    print(f"  brand_mentioned: {mentioned} ({rate:.1f}%)   target_cited: {cited} ({cite_rate:.1f}%)")
    print(f"  completed_at: {s.completed_at}")
    print(f"  VERDICT: {verdict}\n")
