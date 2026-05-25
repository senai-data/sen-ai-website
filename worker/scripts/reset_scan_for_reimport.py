"""Reset a COMPLETED scan back to personas_ready for a clean re-import + relaunch.

Needed when a scan ran on the wrong personas (e.g. auto-gen instead of the
seo-llm import) and already produced results + downstream artifacts. Because
scan_llm_results.question_id is ondelete=SET NULL (not CASCADE), deleting personas
does NOT remove results — they'd pollute the next run. This clears all scan-scoped
derived data and cancels pending/running jobs.

Run AFTER restarting the worker to kill any in-flight job.
    SCAN_ID=<uuid> docker exec senai-worker python /tmp/reset_scan_for_reimport.py
"""
import os, sys
sys.path.insert(0, '/app')
from datetime import datetime
from sqlalchemy import text
from models import SessionLocal, Scan, Job

SCAN_ID = os.environ.get("SCAN_ID", "")
db = SessionLocal()
s = db.query(Scan).filter(Scan.id == SCAN_ID).first()
if not s:
    print(f"NOT FOUND: {SCAN_ID}"); sys.exit(1)

# Cancel any pending/running jobs for this scan (post-scan pipeline).
jobs = db.query(Job).filter(
    Job.scan_id == SCAN_ID, Job.status.in_(("pending", "running"))).all()
for j in jobs:
    j.status = "cancelled"
    j.attempts = max(j.attempts or 0, j.max_attempts or 1)

# Clear scan-scoped derived data (raw SQL: robust to model name drift).
counts = {}
for tbl in ("scan_llm_results", "scan_question_judgments",
            "scan_opportunities", "scan_content_items"):
    try:
        res = db.execute(text(f"DELETE FROM {tbl} WHERE scan_id = :sid"), {"sid": SCAN_ID})
        counts[tbl] = res.rowcount
    except Exception as e:
        counts[tbl] = f"skip ({type(e).__name__})"
        db.rollback()

s.status = "personas_ready"
s.progress_pct = 0
s.progress_message = None
s.summary = None
s.completed_at = None
s.updated_at = datetime.utcnow()
db.commit()

print(f"Scan {SCAN_ID} ({s.domain})")
print(f"  cancelled {len(jobs)} pending/running job(s)")
print(f"  cleared derived: {counts}")
print(f"  status -> personas_ready (summary/completed_at reset)")
