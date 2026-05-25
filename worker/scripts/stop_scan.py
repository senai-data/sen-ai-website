"""Stop an in-flight AI scan: cancel its run_llm_tests job(s) and reset the scan
to personas_ready so personas can be re-imported and the scan relaunched.

run_llm_tests has no mid-loop cancel check, so the caller MUST also restart the
worker after this to kill the in-flight process (a 'cancelled' job is not
re-picked: the poller only claims pending/running).

Run:
    SCAN_ID=<uuid> docker exec senai-worker python /tmp/stop_scan.py
"""
import os, sys
sys.path.insert(0, '/app')
from datetime import datetime
from models import SessionLocal, Scan, Job

SCAN_ID = os.environ.get("SCAN_ID", "")
db = SessionLocal()
s = db.query(Scan).filter(Scan.id == SCAN_ID).first()
if not s:
    print(f"NOT FOUND: {SCAN_ID}"); sys.exit(1)

jobs = db.query(Job).filter(
    Job.scan_id == SCAN_ID,
    Job.job_type == "run_llm_tests",
    Job.status.in_(("pending", "running")),
).all()
for j in jobs:
    j.status = "cancelled"
    j.attempts = max(j.attempts or 0, j.max_attempts or 1)
s.status = "personas_ready"
s.progress_pct = 0
s.progress_message = "Scan stopped — re-importing seo-llm personas"
s.updated_at = datetime.utcnow()
db.commit()
print(f"Scan {SCAN_ID} ({s.domain})")
print(f"  cancelled {len(jobs)} run_llm_tests job(s)")
print(f"  status -> personas_ready")
print("  NOW restart senai-worker to kill the in-flight process.")
