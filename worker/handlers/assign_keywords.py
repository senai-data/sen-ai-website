"""Handler: reassign keywords to topics based on URL membership.

Called when user validates topics (after adding/removing/renaming topics).
Keywords follow their URL's topic assignment.
"""

import logging
from datetime import datetime

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def execute(job_payload: dict, scan_id: str, db: Session) -> dict:
    """Reassign keywords to active topics based on URL → topic mapping."""
    from models import ScanKeyword, ScanTopic, Scan

    scan = db.query(Scan).filter(Scan.id == scan_id).first()
    if not scan:
        raise RuntimeError("Scan not found")

    topics = db.query(ScanTopic).filter(
        ScanTopic.scan_id == scan_id,
        ScanTopic.is_active == True,
    ).all()

    keywords = db.query(ScanKeyword).filter(ScanKeyword.scan_id == scan_id).all()

    # Build URL → topic_id from existing assignments
    # (keywords already have topic_id from classify_topics step)
    # For user-created topics, we need to check if any URLs were manually assigned
    active_topic_ids = {t.id for t in topics}

    # Clear assignments for deactivated topics
    for kw in keywords:
        if kw.topic_id and kw.topic_id not in active_topic_ids:
            kw.topic_id = None

    # Update keyword counts (distinct keyword text, not row count — HaloScan returns 1 row per (kw, url) pair)
    assigned = 0
    for topic in topics:
        topic_kws = [kw for kw in keywords if kw.topic_id == topic.id]
        topic.keyword_count = len({kw.keyword for kw in topic_kws})
        top_kws = sorted(topic_kws, key=lambda k: k.traffic or 0, reverse=True)[:5]
        topic.example_keywords = [k.keyword for k in top_kws]
        assigned += len(topic_kws)

    scan.status = "brands_ready"
    scan.progress_message = "Keywords assigned to topics — validate brands to continue"
    scan.updated_at = datetime.utcnow()

    db.commit()

    logger.info(f"Reassigned: {assigned}/{len(keywords)} keywords across {len(topics)} topics")
    return {
        "assigned": assigned,
        "unassigned": len(keywords) - assigned,
        "topics": {t.name: t.keyword_count for t in topics},
    }
