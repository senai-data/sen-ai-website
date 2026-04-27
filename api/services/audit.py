"""M3: Audit logging service.

Usage:
    from services.audit import audit_log
    audit_log(db, user_id=user.id, action="scan.launch", target_type="scan", target_id=str(scan.id), ip=request.client.host)
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from models import AuditLog

logger = logging.getLogger(__name__)


def audit_log(
    db: Session,
    action: str,
    user_id: Optional[str] = None,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    ip: Optional[str] = None,
    details: Optional[dict] = None,
):
    """Write an audit log entry. Fire-and-forget — never raises."""
    try:
        entry = AuditLog(
            user_id=user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            ip_address=ip,
            details=details or {},
        )
        db.add(entry)
        db.flush()  # flush but don't commit — let the caller's transaction handle it
    except Exception:
        logger.exception(f"Failed to write audit log: {action}")
