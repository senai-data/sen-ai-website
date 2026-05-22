"""Recompute target_cited / target_position / citation_rate for a scan whose
citations were extracted with the buggy path-aware target matcher.

No LLM calls — re-evaluates the stored citations JSONB against the bare-host
target domain, fixes est_site_cible per citation, recomputes the row-level
target_cited + target_position, then refreshes scan.summary.citation_rate.

Run:
    SCAN_ID=<uuid> docker exec senai-worker python /tmp/recompute_target_cited.py
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime

sys.path.insert(0, '/app')

from sqlalchemy.orm.attributes import flag_modified
from models import SessionLocal, Scan, ScanLLMResult

SCAN_ID = os.environ.get("SCAN_ID", "")


def bare_host(domain: str) -> str:
    d = (domain or "").strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = d.split("/")[0].split("?")[0]
    return d.replace("www.", "")


def is_target(citation_domain: str, target: str) -> bool:
    d = bare_host(citation_domain)
    return d == target or d.endswith(f".{target}")


def main():
    if not SCAN_ID:
        print("ERROR: SCAN_ID env var required")
        return 1
    db = SessionLocal()
    scan = db.query(Scan).filter(Scan.id == SCAN_ID).first()
    if not scan:
        print(f"ERROR: scan {SCAN_ID} not found")
        return 1

    target_raw = (scan.config or {}).get("target_domains", [scan.domain])
    target_raw = target_raw[0] if target_raw else scan.domain
    target = bare_host(target_raw)
    print(f"Scan {SCAN_ID} ({scan.domain}) → target host = {target!r}")

    results = db.query(ScanLLMResult).filter(ScanLLMResult.scan_id == SCAN_ID).all()
    print(f"Re-evaluating {len(results)} results…")

    fixed_rows = 0
    now_cited = 0
    for r in results:
        citations = r.citations or []
        changed = False
        target_pos = None
        any_cited = False
        for i, c in enumerate(citations):
            dom = c.get("domaine") or c.get("domain") or ""
            should = is_target(dom, target)
            if bool(c.get("est_site_cible")) != should:
                c["est_site_cible"] = should
                changed = True
            if should and target_pos is None:
                target_pos = i + 1
            if should:
                any_cited = True
        if changed:
            r.citations = citations
            flag_modified(r, "citations")
        if bool(r.target_cited) != any_cited or r.target_position != target_pos:
            r.target_cited = any_cited
            r.target_position = target_pos
            fixed_rows += 1
        if any_cited:
            now_cited += 1

    # Recompute summary.citation_rate (% of results with target_cited)
    total = len(results) or 1
    citation_rate = round(now_cited / total * 100, 1)
    summary = dict(scan.summary or {})
    old_rate = summary.get("citation_rate")
    summary["citation_rate"] = citation_rate
    summary["target_cited"] = now_cited
    scan.summary = summary
    flag_modified(scan, "summary")
    scan.updated_at = datetime.utcnow()

    db.commit()

    print(f"\n=========================")
    print(f"  rows with target_cited/position fixed : {fixed_rows}")
    print(f"  results now target_cited=true         : {now_cited} / {total}")
    print(f"  citation_rate : {old_rate}% → {citation_rate}%")
    print(f"=========================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
