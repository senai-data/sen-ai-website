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
    """Write an audit log entry. Fire-and-forget - never raises, and never
    leaves the caller's session unusable.

    The flush runs inside a SAVEPOINT on purpose. Catching the exception is
    not enough : a failed flush puts the Session in "needs rollback" state,
    so the CALLER then dies on its next query with PendingRollbackError,
    far from the real cause. A nested transaction rolls back the audit row
    alone and leaves the outer transaction intact.

    Incident 2026-07-20 : the placements endpoints passed their arguments
    positionally (shifted by one, a User object landed in `action`). The
    row was already committed, then the 500 surfaced on the response
    serialization - a failure that looked nothing like an audit bug.
    """
    try:
        with db.begin_nested():  # SAVEPOINT - released on success, rolled back on error
            entry = AuditLog(
                user_id=user_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                ip_address=ip,
                details=details or {},
            )
            db.add(entry)
        # No commit here - the caller's transaction still owns the row.
    except Exception:
        logger.exception(f"Failed to write audit log: {action}")
