"""Superadmin endpoints for managing static HTML client deliverable reports.

The /r/{slug}/{filename}.html public path is served by Nginx directly from the
filesystem (see nginx.conf and services/reports_publisher.py). These endpoints
manage the DB metadata + the disk operations.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from models import Report, User, get_db
from services.audit import audit_log
from services.auth_service import get_current_user
from services.rate_limit import limiter
from services.reports_publisher import remove_report, slugify, write_report

router = APIRouter()

REPORT_TTL_DAYS_DEFAULT = 30
REPORT_TTL_DAYS_MAX = 365
REPORT_MAX_BYTES = 10 * 1024 * 1024  # 10 MB — must stay ≤ nginx client_max_body_size
PUBLIC_BASE_URL = "https://sen-ai.fr/r"


def require_superadmin(user: User = Depends(get_current_user)) -> User:
    if not user.is_superadmin:
        raise HTTPException(403, "Superadmin access required")
    return user


# ── Response models ────────────────────────────────────────────────────


class ReportOut(BaseModel):
    id: UUID
    slug: str
    filename: str
    client_label: str
    period_label: str
    file_size: int
    published_at: datetime
    expires_at: datetime
    unpublished_at: Optional[datetime] = None
    url: str


class SuggestionsOut(BaseModel):
    clients: List[str]
    periods: List[str]


def _to_out(r: Report) -> ReportOut:
    return ReportOut(
        id=r.id,
        slug=r.slug,
        filename=r.filename,
        client_label=r.client_label,
        period_label=r.period_label,
        file_size=r.file_size,
        published_at=r.published_at,
        expires_at=r.expires_at,
        unpublished_at=r.unpublished_at,
        url=f"{PUBLIC_BASE_URL}/{r.slug}/{r.filename}",
    )


# ── Endpoints ──────────────────────────────────────────────────────────


def _find_duplicate(
    db: Session,
    client_label: str,
    period_label: str,
    filename: str,
) -> Optional[Report]:
    """Find an active report matching (client, period, filename).

    Comparison uses slugified labels so "Pierre Fabre" / "pierrefabre" match.
    Filename is already slugified by the caller.
    """
    rows = (
        db.query(Report)
        .filter(Report.unpublished_at.is_(None))
        .filter(Report.filename == filename)
        .all()
    )
    target_client = slugify(client_label)
    target_period = slugify(period_label)
    for r in rows:
        if slugify(r.client_label) == target_client and slugify(r.period_label) == target_period:
            return r
    return None


@router.post("/", response_model=ReportOut)
@limiter.limit("20/minute")
async def publish_report(
    request: Request,
    file: UploadFile = File(...),
    client: str = Form(...),
    period: str = Form(...),
    ttl_days: int = Form(REPORT_TTL_DAYS_DEFAULT),
    on_conflict: str = Form("fail"),   # "fail" | "replace" | "keep"
    db: Session = Depends(get_db),
    user: User = Depends(require_superadmin),
):
    """Upload an HTML report. Generates a 12-char slug + injects a noindex meta.

    Deduplication on (client, period, filename):
      - on_conflict="fail" (default) → 409 with `existing_*` details if a duplicate active report exists
      - on_conflict="replace"        → unpublish the existing one (new URL), then proceed
      - on_conflict="keep"           → proceed regardless (parallel URLs both live)
    """
    if not file.filename or not file.filename.lower().endswith(".html"):
        raise HTTPException(400, "File must have a .html extension")

    body = await file.read()
    if len(body) == 0:
        raise HTTPException(400, "Empty file")
    if len(body) > REPORT_MAX_BYTES:
        raise HTTPException(413, f"File too large (max {REPORT_MAX_BYTES // 1024 // 1024} MB)")

    client_clean = (client or "").strip()
    period_clean = (period or "").strip()
    if not client_clean or not period_clean:
        raise HTTPException(400, "Client and period are required")
    if len(client_clean) > 100 or len(period_clean) > 100:
        raise HTTPException(400, "Client/period labels too long (max 100)")

    if on_conflict not in ("fail", "replace", "keep"):
        raise HTTPException(400, "on_conflict must be one of: fail, replace, keep")

    # Compute the final filename (slugified) up-front for dedup lookup
    final_filename = (slugify(Path(file.filename).stem) or "report") + ".html"

    existing = _find_duplicate(db, client_clean, period_clean, final_filename)

    if existing and on_conflict == "fail":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "duplicate_report",
                "filename": final_filename,
                "client": client_clean,
                "period": period_clean,
                "existing_id": str(existing.id),
                "existing_slug": existing.slug,
                "existing_url": f"{PUBLIC_BASE_URL}/{existing.slug}/{existing.filename}",
                "existing_published_at": existing.published_at.isoformat(),
                "existing_size": existing.file_size,
            },
        )

    if existing and on_conflict == "replace":
        # Disk removal is best-effort (idempotent); DB row gets unpublished_at.
        remove_report(existing.slug, existing.real_path)
        existing.unpublished_at = datetime.utcnow()
        audit_log(
            db,
            action="report.replace.unpublish_old",
            user_id=str(user.id),
            target_type="report",
            target_id=str(existing.id),
            ip=request.client.host if request.client else None,
            details={"slug": existing.slug, "reason": "replaced_by_upload"},
        )
        db.flush()

    try:
        result = write_report(body, file.filename, client_clean, period_clean)
    except ValueError as e:
        raise HTTPException(400, str(e))

    ttl_capped = max(1, min(ttl_days, REPORT_TTL_DAYS_MAX))
    now = datetime.utcnow()
    report = Report(
        slug=result["slug"],
        filename=result["filename"],
        client_label=client_clean[:100],
        period_label=period_clean[:100],
        real_path=result["real_path"],
        file_size=result["file_size"],
        uploaded_by=user.id,
        published_at=now,
        expires_at=now + timedelta(days=ttl_capped),
    )
    db.add(report)
    db.flush()

    audit_log(
        db,
        action="report.publish",
        user_id=str(user.id),
        target_type="report",
        target_id=str(report.id),
        ip=request.client.host if request.client else None,
        details={
            "slug": report.slug,
            "client": report.client_label,
            "period": report.period_label,
            "filename": report.filename,
            "file_size": report.file_size,
            "on_conflict": on_conflict,
            "replaced_id": str(existing.id) if (existing and on_conflict == "replace") else None,
        },
    )
    db.commit()
    db.refresh(report)
    return _to_out(report)


@router.get("/", response_model=List[ReportOut])
async def list_reports(
    include_unpublished: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    q = db.query(Report)
    if not include_unpublished:
        q = q.filter(Report.unpublished_at.is_(None))
    rows = q.order_by(Report.published_at.desc()).all()
    return [_to_out(r) for r in rows]


@router.post("/{report_id}/unpublish", response_model=ReportOut)
@limiter.limit("30/minute")
async def unpublish_report(
    request: Request,
    report_id: UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_superadmin),
):
    report = db.query(Report).filter(Report.id == report_id).first()
    if not report:
        raise HTTPException(404, "Report not found")
    if report.unpublished_at is not None:
        raise HTTPException(400, "Report already unpublished")

    # Disk removal is best-effort. If it fails (file already gone, perms),
    # we still mark the report unpublished in DB so the UI reflects truth.
    remove_report(report.slug, report.real_path)

    report.unpublished_at = datetime.utcnow()
    audit_log(
        db,
        action="report.unpublish",
        user_id=str(user.id),
        target_type="report",
        target_id=str(report.id),
        ip=request.client.host if request.client else None,
        details={"slug": report.slug, "client": report.client_label},
    )
    db.commit()
    db.refresh(report)
    return _to_out(report)


@router.get("/suggestions", response_model=SuggestionsOut)
async def get_suggestions(
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    """Distinct labels seen so far — used by the UI for autocomplete."""
    clients = [
        c for (c,) in db.query(Report.client_label).distinct().order_by(Report.client_label).all()
    ]
    periods = [
        p for (p,) in db.query(Report.period_label).distinct().order_by(Report.period_label.desc()).all()
    ]
    return SuggestionsOut(clients=clients, periods=periods)
