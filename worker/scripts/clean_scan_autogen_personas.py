"""Remove leftover INACTIVE auto-gen personas from a seo-llm-imported scan and
backfill import_brand. The early Avène import deactivated (not deleted) the
auto-gen personas, leaving dead rows; later imports delete them. This aligns an
older scan with the clean pattern. Safe: only deletes inactive non-seo-llm
personas (questions cascade; those personas have no LLM results).

Run:
    SCAN_ID=<uuid> IMPORT_BRAND=avene docker exec senai-worker python /tmp/clean_scan_autogen_personas.py
"""
import os, sys
sys.path.insert(0, '/app')
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.orm.attributes import flag_modified
from models import SessionLocal, Scan

SCAN_ID = os.environ.get("SCAN_ID", "")
IMPORT_BRAND = os.environ.get("IMPORT_BRAND", "")
db = SessionLocal()
s = db.query(Scan).filter(Scan.id == SCAN_ID).first()
if not s:
    print(f"NOT FOUND: {SCAN_ID}"); sys.exit(1)

before = db.execute(text("SELECT COUNT(*) FROM scan_personas WHERE scan_id=:s"), {"s": SCAN_ID}).scalar()
seollm = db.execute(text("SELECT COUNT(*) FROM scan_personas WHERE scan_id=:s AND data->'_source'->>'origin'='seo-llm'"), {"s": SCAN_ID}).scalar()
# Safety: only inactive, non-seo-llm personas (the auto-gen leftovers).
res = db.execute(text(
    "DELETE FROM scan_personas WHERE scan_id=:s AND is_active=false "
    "AND COALESCE(data->'_source'->>'origin','') <> 'seo-llm'"
), {"s": SCAN_ID})
deleted = res.rowcount

if IMPORT_BRAND:
    cfg = dict(s.config or {})
    cfg["import_brand"] = IMPORT_BRAND
    s.config = cfg
    flag_modified(s, "config")
s.updated_at = datetime.utcnow()
db.commit()

after = db.execute(text("SELECT COUNT(*) FROM scan_personas WHERE scan_id=:s"), {"s": SCAN_ID}).scalar()
seollm_after = db.execute(text("SELECT COUNT(*) FROM scan_personas WHERE scan_id=:s AND data->'_source'->>'origin'='seo-llm'"), {"s": SCAN_ID}).scalar()
print(f"Scan {SCAN_ID} ({s.domain})")
print(f"  before: {before} personas ({seollm} seo-llm)")
print(f"  deleted (inactive auto-gen): {deleted}")
print(f"  after: {after} personas ({seollm_after} seo-llm)")
print(f"  import_brand: {IMPORT_BRAND or '(unchanged)'}")
