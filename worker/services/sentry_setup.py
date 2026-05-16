"""Sentry SDK init for the worker process.

The worker version of api/services/sentry_setup.py — same DSN env var (if
the user sets SENTRY_DSN on both .env files), separate `environment` tag
("worker" vs "api") so the dashboard can distinguish them in a single
project.

No before_send filter here : the worker has no 4xx noise to drop. Every
exception that bubbles out of a handler is interesting (the main loop
catches and retries, but Sentry sees it before that).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def init_sentry() -> None:
    """Initialize sentry_sdk if SENTRY_DSN is configured; otherwise log a warning.

    Worker doesn't load pydantic-settings the same way the API does — we
    read straight from os.environ to avoid touching worker/config.py
    Settings model just for two optional vars.
    """
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn:
        logger.warning("SENTRY_DSN not set — error reporting disabled (worker)")
        return

    try:
        import sentry_sdk
    except ImportError:
        logger.warning("sentry_sdk not installed — error reporting disabled (worker)")
        return

    environment = os.environ.get("SENTRY_ENVIRONMENT") or "production"
    sentry_sdk.init(
        dsn=dsn,
        environment=f"{environment}-worker",
        traces_sample_rate=0.0,
        send_default_pii=False,
    )
    logger.info(f"Sentry initialized (env={environment}-worker)")
