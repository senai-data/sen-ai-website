"""Sentry SDK init for the API process.

Goal : capture unhandled exceptions + 500-class errors silently in prod so
Phase C Article gen (12-15j of LLM-heavy code) doesn't ship blind. No
performance traces by default — we only need error reporting at this stage.

Init is a no-op when SENTRY_DSN is empty (mirrors the RESEND_API_KEY
pattern : optional dependency, log warning, keep running). Useful for
local dev where you don't want every divide-by-zero to ping the dashboard.

Public surface :
    init_sentry()  — call once at FastAPI startup.

before_send drops the 4xx noise that would otherwise saturate the free
tier — see _before_send below for the exact rules.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _before_send(event, hint):
    """Filter out expected 4xx noise before sending to Sentry.

    The signal we want : unhandled exceptions and 5xx server errors.
    The noise we drop :
      - HTTPException with status_code < 500 (auth challenges, validation
        rejections, "not found" on legitimately missing rows). These are
        normal user-error paths, not bugs.
      - The literal "Not authenticated" detail from FastAPI's OAuth2 / bearer
        dependency — fires on every page load by an anonymous user (the
        marketing site polling /api/auth/me, bots, etc.) and would otherwise
        be 90% of event volume.
    """
    exc_info = hint.get("exc_info") if hint else None
    if exc_info:
        exc_type, exc_value, _ = exc_info

        # HTTPException 4xx — drop. starlette's HTTPException is what FastAPI
        # actually raises; check both to be safe across import paths.
        try:
            from fastapi import HTTPException as FastAPIHTTPException
            from starlette.exceptions import HTTPException as StarletteHTTPException
            http_exc_types = (FastAPIHTTPException, StarletteHTTPException)
        except Exception:
            http_exc_types = ()

        if http_exc_types and isinstance(exc_value, http_exc_types):
            status = getattr(exc_value, "status_code", 500)
            if status < 500:
                return None
            detail = str(getattr(exc_value, "detail", "") or "")
            if detail.strip().lower() == "not authenticated":
                return None

    return event


def init_sentry() -> None:
    """Initialize sentry_sdk if SENTRY_DSN is configured; otherwise log a warning."""
    from config import settings

    dsn = (settings.sentry_dsn or "").strip()
    if not dsn:
        logger.warning("SENTRY_DSN not set — error reporting disabled (api)")
        return

    try:
        import sentry_sdk
    except ImportError:
        logger.warning("sentry_sdk not installed — error reporting disabled (api)")
        return

    sentry_sdk.init(
        dsn=dsn,
        environment=settings.sentry_environment or "production",
        # No traces — we only care about errors right now. Bumping this to
        # 0.1 is the path to performance monitoring later.
        traces_sample_rate=0.0,
        # Drop request bodies (may contain secrets, scan content). Headers
        # are still useful for debugging — Sentry scrubs Authorization auto.
        send_default_pii=False,
        before_send=_before_send,
    )
    logger.info(f"Sentry initialized (env={settings.sentry_environment or 'production'}, api)")
