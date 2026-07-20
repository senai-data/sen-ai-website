"""Handler : Placements module - match published article URLs against
scan_llm_results.citations[].

For every placement of the scan's lineage (placements attach to the ROOT
scan), compare each citation of the target rescan(s) using the 4-tier
matcher in services/url_matching.py (exact / variant / prefix / domain)
and persist :

  placement_hits       - detail rows for exact/variant/prefix (best level
                         per (placement, slr) - UNIQUE constraint).
  placement_scan_stats - one row per (placement, rescan, provider), ALWAYS
                         written even at zero hits so the timeline has
                         explicit zero points. "cited in X of N runs" :
                         runs_total is the sampling depth (max runs per
                         question for the provider), runs_with_hit the best
                         per-question hit count ; per-question detail in
                         matched_questions.

Gemini grounding redirect citations (vertexaisearch...) are resolved through
url_redirect_cache : one GET without following the redirect, Location header
only (never fetches the target - no SSRF surface), max 3 attempts lifetime.

Idempotent : DELETE + INSERT scoped to (rescan, lineage placements) - a
re-run converges to the same state. Zero LLM, zero credit.

Payload :
  full (bool)  - match every completed scan of the lineage (backfill after
                 an import). Default : only the job's scan_id.

Job type is in POST_SCAN_AUDIT_JOB_TYPES : a failure here NEVER cascades to
scan.status='failed' (Sprint 7 incident class).
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import text as _text
from sqlalchemy.orm import Session

from services.url_matching import (
    build_index,
    is_redirect_url,
    match_citation,
    normalize_url,
    url_hash,
)

logger = logging.getLogger(__name__)

REDIRECT_MAX_ATTEMPTS = 3
REDIRECT_TIMEOUT_SECONDS = 10.0
_MATCH_LEVEL_RANK = {"exact": 0, "variant": 1, "prefix": 2}
MAX_MATCHED_QUESTIONS = 10


def _citation_url(citation: dict) -> str:
    """Citation objects carry url ; legacy variants may only have domaine."""
    return (citation.get("url") or "").strip()


def _resolve_redirects(db: Session, urls: set[str]) -> dict[str, str | None]:
    """Resolve grounding redirect URLs through url_redirect_cache.

    Returns {source_url: resolved_url or None}. Network = one GET per cache
    miss, redirects NOT followed, Location header only. Commits per row so a
    single bad URL never rolls back the batch (MR.2 foot-gun #9).
    """
    resolved: dict[str, str | None] = {}
    if not urls:
        return resolved

    hashes = {url_hash(u): u for u in urls}
    # CAST(... AS text[]) / uuid[] is mandatory on every ANY() below : psycopg2
    # sends a Python list as an untyped array and PG then fails with
    # "operator does not exist: uuid = text" (same foot-gun as MR.1 #5).
    rows = db.execute(
        _text(
            "SELECT url_hash, resolved_url, status, attempts FROM url_redirect_cache "
            "WHERE url_hash = ANY(CAST(:hashes AS text[]))"
        ),
        {"hashes": list(hashes.keys())},
    ).fetchall()
    cached = {r[0]: {"resolved_url": r[1], "status": r[2], "attempts": r[3]} for r in rows}

    to_fetch = []
    for h, source in hashes.items():
        entry = cached.get(h)
        if entry is None:
            to_fetch.append((h, source))
        elif entry["status"] == "resolved":
            resolved[source] = entry["resolved_url"]
        elif entry["attempts"] < REDIRECT_MAX_ATTEMPTS:
            to_fetch.append((h, source))
        else:
            resolved[source] = None

    if not to_fetch:
        return resolved

    import httpx

    with httpx.Client(follow_redirects=False, timeout=REDIRECT_TIMEOUT_SECONDS) as client:
        for h, source in to_fetch:
            target = None
            try:
                response = client.get(source)
                location = response.headers.get("location") or ""
                if location.startswith("http"):
                    target = location
            except Exception:
                target = None
            resolved[source] = target
            try:
                db.execute(
                    _text(
                        "INSERT INTO url_redirect_cache (url_hash, source_url, resolved_url, status, attempts, last_attempt_at) "
                        "VALUES (:h, :src, :resolved, :status, 1, NOW()) "
                        "ON CONFLICT (url_hash) DO UPDATE SET "
                        "resolved_url = COALESCE(EXCLUDED.resolved_url, url_redirect_cache.resolved_url), "
                        "status = CASE WHEN EXCLUDED.resolved_url IS NOT NULL THEN 'resolved' ELSE url_redirect_cache.status END, "
                        "attempts = url_redirect_cache.attempts + 1, "
                        "last_attempt_at = NOW()"
                    ),
                    {
                        "h": h,
                        "src": source,
                        "resolved": target,
                        "status": "resolved" if target else "failed",
                    },
                )
                db.commit()
            except Exception:
                db.rollback()
                logger.warning("url_redirect_cache upsert failed for %s", source[:80], exc_info=True)

    return resolved


def _match_one_scan(db: Session, target_scan_id: str, placements: list, index: dict) -> dict:
    """Match one rescan's citations against the lineage placements and
    rewrite its placement_hits / placement_scan_stats rows."""
    rows = db.execute(
        _text(
            "SELECT id, question_id, provider, run_index, citations, created_at "
            "FROM scan_llm_results "
            "WHERE scan_id = :sid AND run_index >= 1 "
            "AND citations IS NOT NULL AND jsonb_typeof(citations) = 'array'"
        ),
        {"sid": target_scan_id},
    ).fetchall()

    # Resolve grounding redirects first (cache-backed).
    redirect_urls = set()
    for row in rows:
        for citation in (row[4] or []):
            url = _citation_url(citation)
            if url and is_redirect_url(url):
                redirect_urls.add(url)
    redirect_map = _resolve_redirects(db, redirect_urls)

    placement_ids = [str(p.id) for p in placements]
    placement_domains = {str(p.id): p.domain for p in placements}

    # hits : best level per (placement, slr)
    hits: dict[tuple, dict] = {}
    # per provider aggregation
    providers: dict[str, dict] = {}

    for row in rows:
        slr_id, question_id, provider, run_index, citations, created_at = row
        prov = providers.setdefault(provider, {
            "question_runs": {},          # question_id -> set(run_index)
            "hit_question_runs": {},      # placement_id -> {question_id -> set(run_index)}
            "domain_counts": {},          # placement_id -> int
            "unresolved": 0,
            "best_position": {},          # placement_id -> int
        })
        prov["question_runs"].setdefault(str(question_id), set()).add(run_index)

        for position, citation in enumerate(citations or [], start=1):
            url = _citation_url(citation)
            if not url:
                continue
            resolved_from_redirect = False
            if is_redirect_url(url):
                resolved = redirect_map.get(url)
                if not resolved:
                    prov["unresolved"] += 1
                    continue
                url = resolved
                resolved_from_redirect = True
            citation_position = citation.get("position_dans_reponse") or position

            for pid, level in match_citation(index, url):
                if level == "domain":
                    prov["domain_counts"][pid] = prov["domain_counts"].get(pid, 0) + 1
                    continue
                key = (pid, str(slr_id))
                existing = hits.get(key)
                if existing is None or _MATCH_LEVEL_RANK[level] < _MATCH_LEVEL_RANK[existing["match_level"]]:
                    hits[key] = {
                        "placement_id": pid,
                        "slr_id": str(slr_id),
                        "question_id": str(question_id) if question_id else None,
                        "provider": provider,
                        "run_index": run_index,
                        "match_level": level,
                        "matched_url": url[:2000],
                        "resolved_from_redirect": resolved_from_redirect,
                        "citation_position": citation_position,
                        "result_created_at": created_at,
                    }
                if level in ("exact", "variant"):
                    per_q = prov["hit_question_runs"].setdefault(pid, {})
                    per_q.setdefault(str(question_id), set()).add(run_index)
                    best = prov["best_position"].get(pid)
                    if best is None or citation_position < best:
                        prov["best_position"][pid] = citation_position

    # Question labels for matched_questions payloads (single IN query).
    matched_question_ids = set()
    for prov in providers.values():
        for per_q in prov["hit_question_runs"].values():
            matched_question_ids.update(q for q in per_q if q and q != "None")
    question_text = {}
    if matched_question_ids:
        q_rows = db.execute(
            _text("SELECT id, question FROM scan_questions WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": list(matched_question_ids)},
        ).fetchall()
        question_text = {str(r[0]): r[1] for r in q_rows}

    scan_created_at = db.execute(
        _text("SELECT created_at FROM scans WHERE id = :sid"), {"sid": target_scan_id}
    ).scalar() or datetime.utcnow()

    # Idempotent rewrite scoped to (rescan, lineage placements).
    db.execute(
        _text(
            "DELETE FROM placement_hits WHERE scan_id = CAST(:sid AS uuid) "
            "AND placement_id = ANY(CAST(:pids AS uuid[]))"
        ),
        {"sid": target_scan_id, "pids": placement_ids},
    )
    db.execute(
        _text(
            "DELETE FROM placement_scan_stats WHERE scan_id = CAST(:sid AS uuid) "
            "AND placement_id = ANY(CAST(:pids AS uuid[]))"
        ),
        {"sid": target_scan_id, "pids": placement_ids},
    )

    for hit in hits.values():
        db.execute(
            _text(
                "INSERT INTO placement_hits (placement_id, slr_id, scan_id, question_id, provider, run_index, "
                "match_level, matched_url, resolved_from_redirect, citation_position, result_created_at) "
                "VALUES (:placement_id, :slr_id, :sid, :question_id, :provider, :run_index, "
                ":match_level, :matched_url, :resolved_from_redirect, :citation_position, :result_created_at)"
            ),
            dict(hit, sid=target_scan_id),
        )

    import json

    stats_written = 0
    for provider, prov in providers.items():
        runs_total = max((len(runs) for runs in prov["question_runs"].values()), default=0)
        for pid in placement_ids:
            per_q = prov["hit_question_runs"].get(pid, {})
            runs_with_hit = max((len(runs) for runs in per_q.values()), default=0)
            matched_questions = [
                {
                    "question_id": qid,
                    "question": question_text.get(qid, ""),
                    "runs": len(runs),
                    "of": len(prov["question_runs"].get(qid, ())) or runs_total,
                }
                for qid, runs in sorted(per_q.items(), key=lambda item: -len(item[1]))[:MAX_MATCHED_QUESTIONS]
            ]
            db.execute(
                _text(
                    "INSERT INTO placement_scan_stats (placement_id, scan_id, provider, runs_total, runs_with_hit, "
                    "domain_citation_count, unresolved_redirects, best_position, matched_questions, scan_created_at) "
                    "VALUES (:pid, :sid, :provider, :runs_total, :runs_with_hit, "
                    ":domain_count, :unresolved, :best_position, CAST(:matched_questions AS jsonb), :scan_created_at)"
                ),
                {
                    "pid": pid,
                    "sid": target_scan_id,
                    "provider": provider,
                    "runs_total": runs_total,
                    "runs_with_hit": runs_with_hit,
                    "domain_count": prov["domain_counts"].get(pid, 0),
                    "unresolved": prov["unresolved"],
                    "best_position": prov["best_position"].get(pid),
                    "matched_questions": json.dumps(matched_questions),
                    "scan_created_at": scan_created_at,
                },
            )
            stats_written += 1

    db.commit()
    return {"rows": len(rows), "hits": len(hits), "stats": stats_written}


def execute(job_payload: dict, scan_id: str, db: Session) -> dict:
    """Match placements for this rescan (default) or the whole lineage
    (payload.full = backfill after adding/importing placements)."""
    from models import Scan, ScanPlacement

    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan:
        raise RuntimeError("Scan not found")

    root_id = scan.parent_scan_id or scan.id
    placements = (
        db.query(ScanPlacement).filter(ScanPlacement.scan_id == root_id).all()
    )
    if not placements:
        return {"skipped": "no_placements", "root_id": str(root_id)}

    index = build_index([{"id": str(p.id), "url": p.url} for p in placements])

    if job_payload.get("full"):
        target_rows = db.execute(
            _text(
                "SELECT id FROM scans WHERE (id = :root OR parent_scan_id = :root) "
                "AND status = 'completed' ORDER BY created_at"
            ),
            {"root": str(root_id)},
        ).fetchall()
        target_ids = [str(r[0]) for r in target_rows]
    else:
        target_ids = [str(scan_id)]

    totals = {"scans": 0, "rows": 0, "hits": 0, "stats": 0}
    for target_id in target_ids:
        outcome = _match_one_scan(db, target_id, placements, index)
        totals["scans"] += 1
        totals["rows"] += outcome["rows"]
        totals["hits"] += outcome["hits"]
        totals["stats"] += outcome["stats"]

    logger.info(
        "match_placements root=%s placements=%d scans=%d hits=%d",
        root_id, len(placements), totals["scans"], totals["hits"],
    )
    totals["placements"] = len(placements)
    totals["root_id"] = str(root_id)
    return totals
