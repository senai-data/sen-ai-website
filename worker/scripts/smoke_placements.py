"""Smoke: Placements module end-to-end on a real lineage.

Run inside the worker container:
  docker exec -w /app -e PYTHONPATH=/app senai-worker python /app/scripts/smoke_placements.py <root_scan_id> <urls_file>

Ground truth for the A-Derma lineage (verified by hand 2026-07-17, including
resolving all 232 vertexaisearch redirects): NONE of the 18 published press
articles is cited. The smoke asserts 0 hits on them, then runs a POSITIVE
control with a URL known to be cited (typed without www and without trailing
slash) which MUST produce exact hits - proving the matcher isn't just
returning zero for everything.

Read-only on existing tables. Cleans up after itself.
"""
import sys

sys.path.insert(0, "/app")

from handlers import match_placements  # noqa: E402
from models import ScanPlacement, SessionLocal  # noqa: E402
from services.url_matching import normalize_url  # noqa: E402
from sqlalchemy import text as _text  # noqa: E402


def add_placement(db, root_id, url, source="manual"):
    norm = normalize_url(url)
    existing = (
        db.query(ScanPlacement)
        .filter(ScanPlacement.scan_id == root_id, ScanPlacement.url_canonical == norm["canonical"])
        .first()
    )
    if existing:
        return existing
    placement = ScanPlacement(
        scan_id=root_id,
        url=url,
        url_canonical=norm["canonical"],
        url_path_key=norm["path_key"],
        domain=norm["registrable_domain"],
        source=source,
    )
    db.add(placement)
    db.commit()
    return placement


def report(db, root_id, label):
    rows = db.execute(_text(
        "SELECT p.url, s.provider, s.runs_with_hit, s.runs_total, s.domain_citation_count, "
        "s.unresolved_redirects, s.best_position "
        "FROM scan_placements p JOIN placement_scan_stats s ON s.placement_id = p.id "
        "WHERE p.scan_id = :root ORDER BY s.runs_with_hit DESC, p.url, s.provider"
    ), {"root": root_id}).fetchall()
    print("\n=== " + label + " ===")
    cited = 0
    for url, provider, hit, total, domain_count, unresolved, best in rows:
        if hit:
            cited += 1
            print("  CITED  %-9s %d/%d runs  best_pos=%s  %s" % (provider, hit, total, best, url[:70]))
    print("  rows=%d  cited_rows=%d" % (len(rows), cited))
    domain_signal = db.execute(_text(
        "SELECT p.url, SUM(s.domain_citation_count) FROM scan_placements p "
        "JOIN placement_scan_stats s ON s.placement_id = p.id "
        "WHERE p.scan_id = :root GROUP BY p.url HAVING SUM(s.domain_citation_count) > 0 "
        "ORDER BY 2 DESC LIMIT 5"
    ), {"root": root_id}).fetchall()
    for url, count in domain_signal:
        print("  domain-only signal: %d citations of the media  %s" % (count, url[:60]))
    return cited


def main():
    root_id = sys.argv[1]
    urls_file = sys.argv[2]
    db = SessionLocal()

    urls = [u.strip() for u in open(urls_file) if u.strip()]
    print("Loading %d published URLs onto lineage %s" % (len(urls), root_id))
    for url in urls:
        add_placement(db, root_id, url)

    last_scan = db.execute(_text(
        "SELECT id FROM scans WHERE (id = :root OR parent_scan_id = :root) AND status = 'completed' "
        "ORDER BY created_at DESC LIMIT 1"
    ), {"root": root_id}).scalar()

    result = match_placements.execute({"full": True}, str(last_scan), db)
    print("matcher:", result)
    cited = report(db, root_id, "NEGATIVE CONTROL - 18 published press articles")

    if cited:
        print("\n!! UNEXPECTED: ground truth says none of these are cited")
    else:
        print("\nOK negative control: zero citations, matches the manual audit")

    # POSITIVE CONTROL : a URL we know IS cited, typed sloppily (no www, no
    # trailing slash) - must still match exactly.
    known = db.execute(_text(
        "SELECT c->>'url' FROM scan_llm_results slr "
        "CROSS JOIN LATERAL jsonb_array_elements(slr.citations) c "
        "WHERE slr.scan_id = :sid AND jsonb_typeof(slr.citations) = 'array' "
        "AND c->>'domaine' = 'www.ameli.fr' AND length(c->>'url') > 40 LIMIT 1"
    ), {"sid": str(last_scan)}).scalar()
    if not known:
        print("no ameli.fr citation found for the positive control")
        return

    sloppy = known.replace("https://www.", "https://").rstrip("/")
    print("\npositive control URL as stored in citations : " + known[:90])
    print("typed by the user (no www, no slash)        : " + sloppy[:90])
    control = add_placement(db, root_id, sloppy)
    match_placements.execute({"full": True}, str(last_scan), db)

    rows = db.execute(_text(
        "SELECT provider, runs_with_hit, runs_total, best_position FROM placement_scan_stats "
        "WHERE placement_id = :pid AND runs_with_hit > 0 ORDER BY provider"
    ), {"pid": str(control.id)}).fetchall()
    print("\n=== POSITIVE CONTROL ===")
    for provider, hit, total, best in rows:
        print("  CITED  %-9s %d/%d runs  best_pos=%s" % (provider, hit, total, best))
    levels = db.execute(_text(
        "SELECT match_level, count(*) FROM placement_hits WHERE placement_id = :pid GROUP BY 1"
    ), {"pid": str(control.id)}).fetchall()
    print("  hit levels:", dict(levels))
    print("  %s" % ("OK positive control" if rows else "!! FAILED - matcher returns zero for a known-cited URL"))

    # Cleanup the positive control only (the 18 stay for the UI smoke).
    db.delete(control)
    db.commit()
    left = db.execute(_text(
        "SELECT count(*) FROM placement_hits ph JOIN scan_placements p ON p.id = ph.placement_id "
        "WHERE p.scan_id = :root"
    ), {"root": root_id}).scalar()
    print("\ncleanup: control removed, remaining hit rows on lineage = %d (cascade OK)" % left)
    db.close()


if __name__ == "__main__":
    main()
