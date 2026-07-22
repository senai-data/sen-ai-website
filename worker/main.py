"""Worker main loop - polls PostgreSQL for pending jobs and executes handlers."""

import logging
import os
import signal
import time
from datetime import datetime, timedelta

import httpx
from sqlalchemy import desc, text
from sqlalchemy.orm import Session

from config import settings
from models import ClientCredit, Job, SessionLocal
from services.healthcheck import ping_heartbeat, ping_t14_sweep
from services.sentry_setup import init_sentry

# Sentry init at import time so import-time errors (handler registry, etc.)
# also reach the dashboard. No-op if SENTRY_DSN is empty.
init_sentry()


def _format_user_error(exc: Exception) -> str:
    """Convert a raw exception into a user-facing scan.error_message.

    httpx.HTTPStatusError stringifies as 'Client error 'XYZ' for url ...' which
    is meaningless to end users. Most provider errors carry a JSON body with a
    human-readable message (`{"error": {"type": ..., "message": ...}}`) - we
    extract it and prepend the provider name so the user knows where to act.

    Special-cases the common billing/quota error so it reads as a clear billing
    issue rather than a vague rate-limit-y message.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            body = exc.response.json()
            err = body.get("error", {}) if isinstance(body, dict) else {}
            provider_msg = (err.get("message") or "").strip()
            err_type = err.get("type", "")
            url = str(exc.request.url).lower()
            if "anthropic.com" in url:
                provider = "Anthropic (Claude)"
            elif "openai.com" in url:
                provider = "OpenAI"
            elif "googleapis.com" in url or "generativelanguage" in url:
                provider = "Gemini"
            else:
                provider = "AI provider"
            msg_lower = provider_msg.lower()
            if any(kw in msg_lower for kw in ("credit balance", "billing", "quota", "insufficient_quota")):
                return (
                    f"{provider} billing/quota issue: {provider_msg}\n"
                    f"Recharge your {provider} account, then click Retry."
                )
            if exc.response.status_code == 429:
                return f"{provider} rate-limited: {provider_msg or 'too many requests'} - try again in a few minutes."
            return f"{provider} error ({err_type or exc.response.status_code}): {provider_msg[:300]}"
        except Exception:
            pass
    return str(exc)[:500]

# H4: stuck-job sweep config. The longest legitimate handler is run_llm_tests
# (LLM calls per question × providers - can run 30-60 min for big scans).
# 2h is a comfortable cap; anything past that is definitely worker-killed.
STUCK_JOB_TIMEOUT_HOURS = 2
# T7 (robustesse worker): run_llm_tests bumps scan.updated_at on every test, so
# a genuinely-progressing scan is never mistaken for stuck. A run whose scan has
# not advanced for this long is hung (a frozen provider call - LLM timeout is
# 120s, so 15 min of zero progress means the worker itself died or wedged) or
# orphaned - reclaim it fast instead of waiting out STUCK_JOB_TIMEOUT_HOURS.
# Only run_llm_tests is progress-tracked; every other job type keeps the start-
# age cap (their runtimes are bounded, and a 5-15 min content gen carries no
# per-progress heartbeat so it must never be reclaimed mid-flight).
PROGRESS_STALL_MINUTES = 15
CLEANUP_INTERVAL_SECONDS = 300  # run sweep at most every 5 min
_LAST_CLEANUP_TS = 0.0

# --- Graceful shutdown (T5, robustesse worker) ---
# docker stop / a deploy sends SIGTERM. With no handler, the default action
# kills the process mid-job, leaving run_llm_tests' row stuck in 'running' with
# no live worker = a ZOMBIE that freezes the user's scan until the stuck-job
# sweep reclaims it (the 2026-07-22 Elgydium-at-24% incident). Instead we catch
# SIGTERM/SIGINT and, if a job is executing, atomically re-pend it (status back
# to 'pending', started_at cleared) so a fresh worker resumes it cleanly -
# run_llm_tests is resumable (T6), so no LLM spend is wasted. poll_and_execute
# records the in-flight job id here; the handler resets that row on its OWN db
# connection (the main thread is blocked deep inside the synchronous handler, so
# we cannot wait for the loop to notice a flag between jobs) and exits within
# docker's stop_grace_period.
_current_job_id = None
_shutting_down = False


def _graceful_shutdown(signum, frame):
    """SIGTERM/SIGINT handler: re-pend the in-flight job, then exit fast.

    Runs in the main thread (Python delivers signals there), which is blocked
    inside the running handler - so we do the re-pend right here on a fresh DB
    connection rather than deferring to the poll loop, then os._exit(0). We must
    NOT raise/sys.exit: unwinding through the handler's `with ThreadPoolExecutor`
    would call shutdown(wait=True) and block for the full job duration, exactly
    what we're trying to avoid.
    """
    global _shutting_down
    if _shutting_down:
        # A second signal (or docker escalating) - bail immediately. The
        # stuck-job sweep is the backstop if the re-pend below didn't commit.
        os._exit(0)
    _shutting_down = True
    sig_name = signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
    job_id = _current_job_id
    logger.info(f"Received {sig_name} - graceful shutdown (in-flight job={job_id})")

    if job_id is not None:
        # Re-pend on a FRESH connection so a half-open main-thread transaction
        # can't corrupt this write. Attempt-neutral (attempts-1) so repeated
        # deploys never exhaust a scan's retries; payload.resume flags intent for
        # the logs. The status guard makes it a no-op if the job already finished
        # between the signal firing and here.
        try:
            db = SessionLocal()
            try:
                db.execute(
                    text("""
                        UPDATE jobs
                        SET status = 'pending',
                            started_at = NULL,
                            attempts = GREATEST(COALESCE(attempts, 1) - 1, 0),
                            payload = COALESCE(payload, '{}'::jsonb) || '{"resume": true}'::jsonb
                        WHERE id = :id AND status = 'running'
                    """),
                    {"id": str(job_id)},
                )
                db.commit()
                logger.info(f"Re-pended in-flight job {job_id} for clean resume")
            finally:
                db.close()
        except Exception:
            # Never block shutdown on the reset - the progress-stall sweep (T7)
            # will reclaim the row if this failed.
            logger.exception(f"Graceful re-pend of job {job_id} failed")

    os._exit(0)

# Phase E Pilier 7 - T+14 post-publish measurement loop.
# Every hour we sweep for content items that were published ≥ N days ago and
# haven't had a post-publish LLM measurement yet, and enqueue a
# refresh_ai_snapshot job for each. The handler already exists (Pilier 5),
# so this is purely an automated trigger.
POST_PUBLISH_MEASUREMENT_DELAY_DAYS = 14
POST_PUBLISH_SCAN_INTERVAL_SECONDS = 3600  # check at most every 1 hour
_LAST_POST_PUBLISH_SCAN_TS = 0.0

# Phase MR.1 - Media catalog discovery loop.
# Every 24h we enqueue a discover_media_catalog job which re-aggregates
# scan_llm_results.citations into media_catalog and asks LinkFinder for
# prices on stale rows. Idempotent handler (additive UPSERT + 7-day
# LinkFinder recheck throttle), so re-running at restart is harmless.
MEDIA_CATALOG_SWEEP_INTERVAL_SECONDS = 86400  # 24h
_LAST_MEDIA_CATALOG_SWEEP_TS = 0.0

# S15.4 part B - auto-rescan cron sweep. Polls every 5 min for scans with
# weekly/monthly schedule whose next_run_at has elapsed and triggers the
# auto-rescan endpoint. Failures (insufficient credits, transient errors)
# defer next_run_at by AUTO_RESCAN_RETRY_DEFER_SECONDS so we don't hammer.
AUTO_RESCAN_SWEEP_INTERVAL_SECONDS = 300        # 5 min
AUTO_RESCAN_RETRY_DEFER_SECONDS = 86400         # 24h on failure
AUTO_RESCAN_BATCH_LIMIT = 10                    # cap fired-per-tick to bound the call burst
_LAST_AUTO_RESCAN_SWEEP_TS = 0.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("worker")

# Handler registry
HANDLERS = {}


def register_handler(job_type: str):
    def decorator(func):
        HANDLERS[job_type] = func
        return func
    return decorator


def load_handlers():
    from handlers import (fetch_keywords, classify_topics, assign_keywords,
                          generate_personas, generate_persona_questions,
                          classify_question_intent,
                          judge_question_responses,
                          run_llm_tests, generate_editorial,
                          detect_competitors, generate_opportunities, cleanup_brands,
                          generate_domain_brief, generate_client_brief,
                          generate_brand_brief,
                          generate_faq, generate_article,
                          materialize_content_items,
                          rematch_target_url, discover_trust_sources,
                          crawl_brand_sitemap, fetch_brand_pages,
                          embed_brand_pages, purge_stale_pages,
                          refresh_ai_snapshot,
                          discover_media_catalog,
                          suggest_media,
                          measure_publish_outcome,
                          check_brand_wikipedia,
                          audit_scan_pages,
                          audit_scan_schemas,
                          audit_competitor_pages,
                          audit_reddit_threads,
                          build_pr_outreach,
                          audit_internal_links,
                          audit_youtube_creators,
                          build_crisis_radar,
                          judge_sentiment,
                          match_placements)  # noqa: F401
    HANDLERS["fetch_keywords"] = fetch_keywords.execute
    HANDLERS["classify_topics"] = classify_topics.execute
    HANDLERS["assign_keywords"] = assign_keywords.execute
    HANDLERS["detect_competitors"] = detect_competitors.execute
    HANDLERS["generate_personas"] = generate_personas.execute
    HANDLERS["generate_persona_questions"] = generate_persona_questions.execute
    HANDLERS["classify_question_intent"] = classify_question_intent.execute
    HANDLERS["judge_question_responses"] = judge_question_responses.execute
    HANDLERS["run_llm_tests"] = run_llm_tests.execute
    HANDLERS["generate_opportunities"] = generate_opportunities.execute
    HANDLERS["generate_editorial"] = generate_editorial.execute
    HANDLERS["cleanup_brands"] = cleanup_brands.execute
    HANDLERS["generate_domain_brief"] = generate_domain_brief.execute
    HANDLERS["generate_client_brief"] = generate_client_brief.execute
    HANDLERS["generate_brand_brief"] = generate_brand_brief.execute
    HANDLERS["generate_faq"] = generate_faq.execute
    HANDLERS["generate_article"] = generate_article.execute
    HANDLERS["materialize_content_items"] = materialize_content_items.execute
    HANDLERS["rematch_target_url"] = rematch_target_url.execute
    HANDLERS["discover_trust_sources"] = discover_trust_sources.execute
    HANDLERS["crawl_brand_sitemap"] = crawl_brand_sitemap.execute
    HANDLERS["fetch_brand_pages"] = fetch_brand_pages.execute
    HANDLERS["embed_brand_pages"] = embed_brand_pages.execute
    HANDLERS["purge_stale_pages"] = purge_stale_pages.execute
    HANDLERS["refresh_ai_snapshot"] = refresh_ai_snapshot.execute
    HANDLERS["discover_media_catalog"] = discover_media_catalog.execute
    HANDLERS["suggest_media"] = suggest_media.execute
    HANDLERS["measure_publish_outcome"] = measure_publish_outcome.execute
    HANDLERS["check_brand_wikipedia"] = check_brand_wikipedia.execute
    HANDLERS["audit_scan_pages"] = audit_scan_pages.execute
    HANDLERS["audit_scan_schemas"] = audit_scan_schemas.execute
    HANDLERS["audit_competitor_pages"] = audit_competitor_pages.execute
    HANDLERS["audit_reddit_threads"] = audit_reddit_threads.execute
    HANDLERS["build_pr_outreach"] = build_pr_outreach.execute
    HANDLERS["audit_internal_links"] = audit_internal_links.execute
    HANDLERS["audit_youtube_creators"] = audit_youtube_creators.execute
    HANDLERS["build_crisis_radar"] = build_crisis_radar.execute
    HANDLERS["judge_sentiment"] = judge_sentiment.execute
    HANDLERS["match_placements"] = match_placements.execute


# Job types that operate on a single content item (one FAQ / article / …)
# rather than on the whole scan pipeline. When one of these fails permanently
# we refund the per-item content_credit and DO NOT cascade to the scan-level
# failure path (which over-refunds the parent scan's scan_credits and marks
# the scan as failed even though it actually completed long ago).
CONTENT_ITEM_JOB_TYPES = {"generate_faq", "generate_article"}

# Post-scan analytical / audit jobs. These run AFTER a scan has reached its
# terminal state ; their failure must NOT cascade back to scan.status='failed'
# (that would blank all sub-tab pages via the layout's status-guard redirect).
# Treated as independent of the scan pipeline lifecycle.
POST_SCAN_AUDIT_JOB_TYPES = {
    "audit_scan_pages",         # Sprint 5
    "audit_scan_schemas",       # Sprint 6
    "audit_competitor_pages",   # Sprint 7
    "audit_reddit_threads",     # Sprint 8
    "build_pr_outreach",        # Sprint 9
    "audit_internal_links",     # Sprint 11
    "audit_youtube_creators",   # Sprint 10
    "build_crisis_radar",       # Sprint 12
    "judge_sentiment",          # Sentiment Judge (anti-false-positive layer)
    "check_brand_wikipedia",    # Sprint 4
    "refresh_ai_snapshot",
    "measure_publish_outcome",
    "suggest_media",
    "match_placements",         # Placements module (migration 062)
}


# Per-content-type credit-ledger description labels. The debit (created at
# enqueue time in api/routers/content_items.py:generate_content) and the
# refund (created here on permanent failure) MUST use the same label so the
# net-aware refund matcher pairs them up correctly. The legacy "FAQ" pattern
# is kept for backward-compatibility with debits made before Phase C.1.
_CONTENT_LABEL_BY_JOB_TYPE = {
    "generate_faq": "FAQ",
    "generate_article": "Article",
}


def _refund_content_item_credit(scan_id, item_id: str, db: Session,
                                 job_type: str = "generate_faq") -> None:
    """Refund the unmatched content_credit debit(s) for one item.

    Net-aware idempotency : sums all debit + refund rows tied to this item.
    The ledger description format is `"<LABEL> generation: <item_id>"` for
    debits and `"Refund <LABEL> generation: <item_id>"` for refunds. We
    match BOTH the FAQ and Article labels (and pair them by item_id only)
    so that :
      - A legacy FAQ item created pre-Phase-C.1 still refunds correctly.
      - An Article item refund logic also picks up its debit row.
      - A future content_type only needs to add one entry to
        _CONTENT_LABEL_BY_JOB_TYPE.

    If the net (sum of debit + refund amounts) is negative, the user is
    owed that amount - insert one refund row. If net is 0 or positive,
    no-op (already fully refunded).

    This handles the retry flow correctly : debit → fail → refund → user
    fixes target_url → debit → fail → refund. Each generate cycle has its
    own debit row, and we always pair them.

    This is a per-item refund, distinct from `_refund_scan_credits` which
    nets all credits tied to a scan. Using the scan-level refund here
    would also refund the parent scan's scan_credits - wrong, because the
    scan itself completed long ago, only this content gen attempt failed.

    The `job_type` arg drives the refund row label only (cosmetic, for
    audit readability) - matching against the existing debit row is
    label-agnostic since we query all known labels at once.
    """
    if not scan_id or not item_id:
        return

    # All known content-type labels we might find in the ledger for this item.
    debit_descs = [f"{label} generation: {item_id}"
                   for label in _CONTENT_LABEL_BY_JOB_TYPE.values()]
    refund_descs = [f"Refund {label} generation: {item_id}"
                    for label in _CONTENT_LABEL_BY_JOB_TYPE.values()]

    rows = (
        db.query(ClientCredit)
        .filter(
            ClientCredit.scan_id == scan_id,
            ClientCredit.credit_type == "content",
            ClientCredit.description.in_(debit_descs + refund_descs),
        )
        .all()
    )
    if not rows:
        logger.info(
            f"No content_credit ledger rows for item {item_id} on scan {scan_id} "
            f"- skipping refund (likely a job enqueued outside the API path)"
        )
        return

    net = sum(r.amount for r in rows)
    if net >= 0:
        logger.info(
            f"Content item {item_id} ledger net = {net}, already fully refunded; "
            f"skipping"
        )
        return

    refund_amount = -net  # positive
    client_id = rows[0].client_id

    # Serialize against concurrent credit ops on this client
    db.execute(
        text("SELECT 1 FROM clients WHERE id = :id FOR UPDATE"),
        {"id": str(client_id)},
    )

    latest = (
        db.query(ClientCredit)
        .filter(
            ClientCredit.client_id == client_id,
            ClientCredit.credit_type == "content",
        )
        .order_by(desc(ClientCredit.created_at))
        .first()
    )
    new_balance = (latest.balance_after if latest else 0) + refund_amount

    # Cosmetic label for the refund row matches the failing job's content_type
    # (so audit can correlate "Refund Article generation:" with the Article
    # debit row 1:1, even though matching is label-agnostic).
    refund_label = _CONTENT_LABEL_BY_JOB_TYPE.get(job_type, "Content")
    refund_desc = f"Refund {refund_label} generation: {item_id}"

    db.add(ClientCredit(
        client_id=client_id,
        credit_type="content",
        amount=refund_amount,
        balance_after=new_balance,
        description=refund_desc,
        scan_id=scan_id,
    ))
    logger.info(
        f"Refunded {refund_amount} content_credit to client {client_id} "
        f"for failed {refund_label} item {item_id}"
    )


def _refund_scan_credits(scan_id, db: Session) -> None:
    """Refund any credits that were debited for this scan.

    Called when a scan permanently fails (attempts >= max_attempts).
    Idempotent: skips if a refund row already exists for this scan.
    Net-aware: refunds the absolute net of debits minus prior refunds, so a
    partial refund history can't double-refund.
    """
    if not scan_id:
        return

    # All ledger rows tied to this scan
    rows = db.query(ClientCredit).filter(ClientCredit.scan_id == scan_id).all()
    if not rows:
        return

    # Net per credit_type (negative = still owed back to user)
    net_by_type: dict[str, int] = {}
    client_id = None
    for r in rows:
        net_by_type[r.credit_type] = net_by_type.get(r.credit_type, 0) + r.amount
        client_id = r.client_id

    for credit_type, net in net_by_type.items():
        if net >= 0:
            continue  # nothing owed (already refunded or never debited)
        refund_amount = -net  # positive

        # Lock the client row to serialize against any concurrent credit op
        db.execute(
            text("SELECT 1 FROM clients WHERE id = :id FOR UPDATE"),
            {"id": str(client_id)},
        )

        # Read latest balance for this (client, type) AFTER lock
        latest = (
            db.query(ClientCredit)
            .filter(
                ClientCredit.client_id == client_id,
                ClientCredit.credit_type == credit_type,
            )
            .order_by(desc(ClientCredit.created_at))
            .first()
        )
        new_balance = (latest.balance_after if latest else 0) + refund_amount

        db.add(ClientCredit(
            client_id=client_id,
            credit_type=credit_type,
            amount=refund_amount,
            balance_after=new_balance,
            description="Refund: scan failed",
            scan_id=scan_id,
        ))
        logger.info(
            f"Refunded {refund_amount} {credit_type} credits to client {client_id} "
            f"for failed scan {scan_id}"
        )


def cleanup_stuck_jobs() -> None:
    """Reclaim jobs stuck in 'running' - killed worker, frozen provider call.

    A worker that dies (OOM, kill -9, container restart, host reboot with no
    SIGTERM) leaves its job in status='running' forever - the in-process retry
    logic only fires when the handler raises in the same process, so a
    hard-killed worker bypasses it entirely. This sweep is the safety net.

    Two detection modes (T7, robustesse worker):
      - `run_llm_tests` is progress-tracked: it bumps scan.updated_at on every
        test, so a scan that has not advanced for PROGRESS_STALL_MINUTES is hung
        or orphaned - caught in ~15 min instead of the old 2 h start-age wait.
        Because it is resumable (T6), a stalled run WITH retries left is
        RE-PENDED (a fresh / restarted worker resumes it, skipping the pairs
        already persisted - zero wasted LLM spend). Out of retries -> fail.
      - every other job type keeps the start-age cap (STUCK_JOB_TIMEOUT_HOURS):
        their runtimes are bounded, they carry no per-progress heartbeat, and a
        5-15 min content gen must never be reclaimed mid-flight. These fail +
        refund (unchanged behavior).

    Cheap no-op most of the time: throttled to CLEANUP_INTERVAL_SECONDS, and
    FOR UPDATE OF j SKIP LOCKED so multiple workers don't fight over a row (and
    we never lock the scans row a live run_llm_tests is busy updating).
    """
    global _LAST_CLEANUP_TS
    now = time.time()
    if now - _LAST_CLEANUP_TS < CLEANUP_INTERVAL_SECONDS:
        return
    _LAST_CLEANUP_TS = now

    db = SessionLocal()
    try:
        now_dt = datetime.utcnow()
        stall_cutoff = now_dt - timedelta(minutes=PROGRESS_STALL_MINUTES)
        age_cutoff = now_dt - timedelta(hours=STUCK_JOB_TIMEOUT_HOURS)
        stuck_rows = db.execute(
            text("""
                SELECT j.id
                FROM jobs j
                LEFT JOIN scans s ON s.id = j.scan_id
                WHERE j.status = 'running'
                  AND (
                    (j.job_type = 'run_llm_tests'
                       AND GREATEST(COALESCE(s.updated_at, j.started_at),
                                    j.started_at) < :stall_cutoff)
                    OR
                    (j.job_type <> 'run_llm_tests'
                       AND j.started_at < :age_cutoff)
                  )
                FOR UPDATE OF j SKIP LOCKED
            """),
            {"stall_cutoff": stall_cutoff, "age_cutoff": age_cutoff},
        ).fetchall()

        if not stuck_rows:
            return

        logger.warning(
            f"Stuck-job sweep: found {len(stuck_rows)} stuck running job(s) - reclaiming"
        )

        from models import Scan  # local import: only loaded if there's work
        from sqlalchemy.orm.attributes import flag_modified

        for (job_id,) in stuck_rows:
            job = db.query(Job).filter(Job.id == job_id).first()
            if not job:
                continue

            elapsed_min = 0
            if job.started_at:
                elapsed_min = int((now_dt - job.started_at).total_seconds() / 60)
            error_msg = (
                f"Job stuck - no progress for {elapsed_min} min "
                f"(worker killed mid-execution or a provider call froze)"
            )

            # Resumable + retries left -> re-pend for a clean resume rather than
            # fail. Only ONE scan-worker runs run_llm_tests (single-threaded), so
            # there is never a concurrent second execution of the same job; T6
            # skips the pairs already written on re-pickup. Attempts climb on
            # re-pickup, so a genuinely-broken scan that keeps stalling still
            # falls through to the terminal path once it runs out of retries
            # (no infinite reclaim loop).
            if (
                job.job_type == "run_llm_tests"
                and (job.attempts or 0) < (job.max_attempts or 3)
            ):
                job.status = "pending"
                job.started_at = None
                job.result = {"reclaimed": True, "reason": error_msg}
                payload = dict(job.payload or {})
                payload["resume"] = True
                job.payload = payload
                flag_modified(job, "payload")  # JSONB in-place mutation
                logger.warning(
                    f"Reclaimed stalled run_llm_tests {job_id} (scan={job.scan_id}, "
                    f"attempts={job.attempts}/{job.max_attempts}) - re-pending for resume"
                )
                continue

            # Terminal path: mark job + scan failed and refund.
            job.status = "failed"
            job.result = {"error": error_msg, "stuck_cleanup": True}
            job.completed_at = now_dt

            scan = db.query(Scan).filter(Scan.id == job.scan_id).first()
            if scan:
                scan.status = "failed"
                scan.error_message = error_msg
                scan.updated_at = now_dt

            try:
                _refund_scan_credits(job.scan_id, db)
            except Exception:
                logger.exception(
                    f"Failed to refund credits for stuck scan {job.scan_id}"
                )

            logger.warning(
                f"Reclaimed stuck job {job_id} (scan={job.scan_id}, "
                f"type={job.job_type}, elapsed={elapsed_min}min) - failed + refunded"
            )

        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Stuck-job sweep failed (will retry next interval)")
    finally:
        db.close()


def enqueue_post_publish_measurements() -> None:
    """Sweep for items ripe for T+14 post-publish AI measurement (Pilier 7).

    An item is ripe when :
      - `published_at` is set (= user clicked Approve / published)
      - `published_at` < now - POST_PUBLISH_MEASUREMENT_DELAY_DAYS
      - No ScanLLMResult exists for the item's question after
        `published_at + (delay - 1) days` (= we haven't already measured)
      - No pending/running refresh_ai_snapshot job is in flight for this item

    Each match enqueues a refresh_ai_snapshot job with `trigger='t14_post_publish'`
    in the payload (informational - the handler is provider-agnostic to its
    caller). Cost ≈ $0.04 per item.

    No global rate cap : we expect 1 measurement per item per 14 days in
    steady state, and the per-question 5/24h refresh cap already protects
    against abuse if a user re-publishes the same item repeatedly.

    Cheap no-op most of the time : runs at most every
    POST_PUBLISH_SCAN_INTERVAL_SECONDS.
    """
    global _LAST_POST_PUBLISH_SCAN_TS
    now = time.time()
    if now - _LAST_POST_PUBLISH_SCAN_TS < POST_PUBLISH_SCAN_INTERVAL_SECONDS:
        return
    _LAST_POST_PUBLISH_SCAN_TS = now

    db = SessionLocal()
    try:
        from models import ScanContentItem, ScanQuestion, ScanLLMResult, Job
        from sqlalchemy import func

        cutoff = datetime.utcnow() - timedelta(days=POST_PUBLISH_MEASUREMENT_DELAY_DAYS)
        items = (
            db.query(ScanContentItem)
            .filter(
                ScanContentItem.published_at.isnot(None),
                ScanContentItem.published_at < cutoff,
            )
            .all()
        )
        if not items:
            return

        enqueued = 0
        for item in items:
            q_text = (item.target_question or "").strip()
            if not q_text:
                continue

            question = (
                db.query(ScanQuestion)
                .filter(
                    ScanQuestion.scan_id == item.scan_id,
                    func.lower(ScanQuestion.question) == q_text.lower(),
                )
                .first()
            )
            if not question:
                continue

            # Already-measured guard : any ScanLLMResult dated after the
            # T+14 threshold means a measurement has happened. Manual refresh
            # before T+14 also satisfies this (the user already has data -
            # auto-rescan would be redundant).
            measurement_cutoff = item.published_at + timedelta(
                days=POST_PUBLISH_MEASUREMENT_DELAY_DAYS - 1,
            )
            has_measurement = (
                db.query(ScanLLMResult)
                .filter(
                    ScanLLMResult.question_id == question.id,
                    ScanLLMResult.created_at > measurement_cutoff,
                )
                .first()
            )
            if has_measurement:
                continue

            # In-flight dedup
            in_flight = (
                db.query(Job)
                .filter(
                    Job.scan_id == item.scan_id,
                    Job.job_type == "refresh_ai_snapshot",
                    Job.status.in_(("pending", "running")),
                )
                .all()
            )
            if any((j.payload or {}).get("item_id") == str(item.id) for j in in_flight):
                continue

            job = Job(
                scan_id=item.scan_id,
                job_type="refresh_ai_snapshot",
                status="pending",
                priority=50,  # background T+14 sweep - never ahead of a user scan
                payload={
                    "item_id": str(item.id),
                    "trigger": "t14_post_publish",
                },
                max_attempts=2,
            )
            db.add(job)
            enqueued += 1
            logger.info(
                f"post_publish_measurement: enqueued T+{POST_PUBLISH_MEASUREMENT_DELAY_DAYS} "
                f"measurement for item {item.id} (published {item.published_at})"
            )

        if enqueued > 0:
            db.commit()
            logger.info(
                f"post_publish_measurement: enqueued {enqueued} job(s) this sweep "
                f"({len(items)} items scanned)"
            )
    except Exception:
        db.rollback()
        logger.exception("enqueue_post_publish_measurements failed")
    finally:
        db.close()

    # Liveness ping outside the try/except so a DB blip doesn't suppress the
    # "cron ran" signal - but still inside the throttle gate above, so it
    # fires at most once per POST_PUBLISH_SCAN_INTERVAL_SECONDS. No-op if
    # HEALTHCHECK_T14_URL is unset.
    ping_t14_sweep()


def enqueue_media_publish_outcomes() -> None:
    """Phase MR.4 #3 - sweep for media-suggested articles ready for T+14
    outcome measurement, enqueue measure_publish_outcome jobs.

    Eligible item :
      - target_url_source = 'media_replacement' (published on a suggested media)
      - published_at < now - POST_PUBLISH_MEASUREMENT_DELAY_DAYS
      - has a ScanLLMResult dated AFTER published_at (the Pilier 7 refresh
        produced the post-publish data point this loop reads)
      - no media_publish_outcome row measured yet
      - no in-flight measure_publish_outcome job

    Runs on the same throttle window as the post-publish sweep (called right
    after it in the main loop). Cheap no-op when nothing is ripe.
    """
    db = SessionLocal()
    try:
        from models import Job
        cutoff = datetime.utcnow() - timedelta(days=POST_PUBLISH_MEASUREMENT_DELAY_DAYS)
        rows = db.execute(text("""
            SELECT sci.id::text AS item_id, sci.scan_id::text AS scan_id
              FROM scan_content_items sci
             WHERE sci.target_url_source = 'media_replacement'
               AND sci.published_at IS NOT NULL
               AND sci.published_at < :cutoff
               AND EXISTS (
                   SELECT 1 FROM scan_questions sq
                     JOIN scan_llm_results slr ON slr.question_id = sq.id
                    WHERE sq.scan_id = sci.scan_id
                      AND lower(sq.question) = lower(sci.target_question)
                      AND slr.created_at > sci.published_at
               )
               AND NOT EXISTS (
                   SELECT 1 FROM media_publish_outcome mpo
                    WHERE mpo.content_item_id = sci.id
                      AND mpo.measured_at IS NOT NULL
               )
             LIMIT 200
        """), {"cutoff": cutoff}).fetchall()
        if not rows:
            return

        in_flight = (
            db.query(Job)
            .filter(
                Job.job_type == "measure_publish_outcome",
                Job.status.in_(("pending", "running")),
            )
            .all()
        )
        in_flight_items = {(j.payload or {}).get("item_id") for j in in_flight}

        enqueued = 0
        for r in rows:
            if r.item_id in in_flight_items:
                continue
            db.add(Job(
                scan_id=r.scan_id,
                job_type="measure_publish_outcome",
                status="pending",
                priority=50,  # background T+14 outcome sweep
                payload={"item_id": r.item_id},
                max_attempts=2,
            ))
            enqueued += 1
        if enqueued:
            db.commit()
            logger.info(f"media_publish_outcome: enqueued {enqueued} outcome job(s)")
    except Exception:
        db.rollback()
        logger.exception("enqueue_media_publish_outcomes failed")
    finally:
        db.close()


def enqueue_media_catalog_discovery() -> None:
    """Daily sweep - enqueue ONE discover_media_catalog job if none in-flight.

    The handler is workspace-wide (scan_id=NULL, payload={}) and idempotent.
    It re-aggregates scan_llm_results.citations into media_catalog, then
    asks LinkFinder for prices on rows whose linkfinder_last_check is
    older than LINKFINDER_RECHECK_DAYS (7d).

    Dedup : skip if any pending/running discover_media_catalog already exists.
    The pair "24h throttle + DB dedup" handles the case where both
    senai-worker and senai-worker-content try to enqueue simultaneously
    after a restart (only senai-worker actually picks the job up - content
    worker's WORKER_JOB_TYPES_INCLUDE excludes it).
    """
    global _LAST_MEDIA_CATALOG_SWEEP_TS
    now = time.time()
    if now - _LAST_MEDIA_CATALOG_SWEEP_TS < MEDIA_CATALOG_SWEEP_INTERVAL_SECONDS:
        return
    _LAST_MEDIA_CATALOG_SWEEP_TS = now

    db = SessionLocal()
    try:
        from models import Job

        # Dedup window covers pending + running AND recently-completed jobs.
        # The "recent" arm prevents the boot-race where two workers (scan +
        # content) start near-simultaneously with _LAST_MEDIA_CATALOG_SWEEP_TS
        # = 0.0 in separate process memory : worker A enqueues + runs (13 s),
        # then worker B's enqueue check fires AFTER worker A's job already
        # completed, so the pending/running dedup misses it. Without this
        # arm, every container restart would burn one extra discovery run.
        recent_cutoff = datetime.utcnow() - timedelta(seconds=MEDIA_CATALOG_SWEEP_INTERVAL_SECONDS // 2)
        in_flight = (
            db.query(Job)
            .filter(
                Job.job_type == "discover_media_catalog",
                (Job.status.in_(("pending", "running")))
                | ((Job.status == "completed") & (Job.completed_at >= recent_cutoff)),
            )
            .order_by(Job.created_at.desc())
            .first()
        )
        if in_flight:
            logger.info(
                f"media_catalog_discovery: skipping enqueue - recent job "
                f"{in_flight.id} (status={in_flight.status})"
            )
            return

        job = Job(
            scan_id=None,
            job_type="discover_media_catalog",
            status="pending",
            priority=50,  # daily maintenance sweep - the 2026-07-21 blocker
            payload={},
            max_attempts=2,
        )
        db.add(job)
        db.commit()
        logger.info(f"media_catalog_discovery: enqueued job {job.id}")
    except Exception:
        db.rollback()
        logger.exception("enqueue_media_catalog_discovery failed")
    finally:
        db.close()


def sweep_due_rescans() -> None:
    """Auto-rescan cron sweep (S15.4 part B).

    Picks up to AUTO_RESCAN_BATCH_LIMIT scans whose schedule is weekly/monthly
    AND next_run_at <= NOW(), and calls the api `POST /scans/{id}/auto-rescan`
    endpoint with the shared service token. The endpoint re-arms next_run_at
    on success ; on credit / transient failure we defer here so the cron
    doesn't loop on the same scan.
    """
    import httpx

    global _LAST_AUTO_RESCAN_SWEEP_TS
    now = time.time()
    if now - _LAST_AUTO_RESCAN_SWEEP_TS < AUTO_RESCAN_SWEEP_INTERVAL_SECONDS:
        return
    _LAST_AUTO_RESCAN_SWEEP_TS = now

    token = (settings.internal_service_token or "").strip()
    if not token:
        logger.debug("auto_rescan_sweep: INTERNAL_SERVICE_TOKEN unset, skipping")
        return

    db = SessionLocal()
    try:
        from models import Scan
        due = (
            db.query(Scan)
            .filter(
                Scan.status == "completed",
                Scan.schedule.in_(("weekly", "monthly")),
                Scan.next_run_at != None,  # noqa: E711 - SQLAlchemy column comparison
                Scan.next_run_at <= datetime.utcnow(),
            )
            .order_by(Scan.next_run_at.asc())
            .limit(AUTO_RESCAN_BATCH_LIMIT)
            .all()
        )
        if not due:
            return

        logger.info(f"auto_rescan_sweep: {len(due)} scan(s) due")
        base_url = settings.api_internal_base_url.rstrip("/")
        for parent in due:
            try:
                resp = httpx.post(
                    f"{base_url}/api/scans/{parent.id}/auto-rescan",
                    headers={"X-Internal-Token": token},
                    timeout=30.0,
                )
                if resp.status_code == 200:
                    logger.info(f"auto_rescan_sweep: rescanned {parent.id} ({parent.domain})")
                elif resp.status_code == 402:
                    logger.warning(
                        f"auto_rescan_sweep: {parent.id} out of credits, "
                        f"deferring {AUTO_RESCAN_RETRY_DEFER_SECONDS}s"
                    )
                    parent.next_run_at = datetime.utcnow() + timedelta(seconds=AUTO_RESCAN_RETRY_DEFER_SECONDS)
                    db.commit()
                elif resp.status_code == 400:
                    # Structural failure (e.g. "no active personas/questions"). A 400
                    # never heals by waiting, so deferring 24h would loop forever and
                    # spam Sentry. Disable the auto-rescan schedule instead ; the user
                    # can re-enable it after fixing the scan.
                    logger.warning(
                        f"auto_rescan_sweep: {parent.id} cannot be rescanned "
                        f"({resp.text[:160]}) - disabling schedule"
                    )
                    parent.schedule = "manual"
                    parent.next_run_at = None
                    db.commit()
                else:
                    logger.error(
                        f"auto_rescan_sweep: {parent.id} returned {resp.status_code}: "
                        f"{resp.text[:200]} - deferring"
                    )
                    parent.next_run_at = datetime.utcnow() + timedelta(seconds=AUTO_RESCAN_RETRY_DEFER_SECONDS)
                    db.commit()
            except Exception:
                logger.exception(f"auto_rescan_sweep: HTTP call for {parent.id} failed - deferring")
                try:
                    parent.next_run_at = datetime.utcnow() + timedelta(seconds=AUTO_RESCAN_RETRY_DEFER_SECONDS)
                    db.commit()
                except Exception:
                    db.rollback()
    except Exception:
        db.rollback()
        logger.exception("sweep_due_rescans failed")
    finally:
        db.close()


def poll_and_execute():
    """Pick one pending job and execute it.

    Optional job_type filter via env (`WORKER_JOB_TYPES_INCLUDE` /
    `WORKER_JOB_TYPES_EXCLUDE`). Empty lists mean "no filter" - legacy
    single-worker behavior. The split-worker setup uses INCLUDE on the
    content-gen worker and EXCLUDE on the scan-pipeline worker so the
    10-min `generate_article` never sits in front of a 3-sec
    `fetch_keywords` (FIFO head-of-line blocking).
    """
    global _current_job_id
    db = SessionLocal()
    try:
        include = settings.job_types_include
        exclude = settings.job_types_exclude

        # Build WHERE fragment for type filter. We deliberately use ANY(:list)
        # rather than IN/NOT IN with a tuple so the SQL stays static - easier
        # to read in logs and no SQL-injection surface (job_type values are
        # registered handler keys, not user input).
        params: dict = {}
        type_clauses = []
        if include:
            type_clauses.append("job_type = ANY(:incl)")
            params["incl"] = include
        if exclude:
            type_clauses.append("NOT (job_type = ANY(:excl))")
            params["excl"] = exclude
        type_sql = (" AND " + " AND ".join(type_clauses)) if type_clauses else ""

        # FOR UPDATE SKIP LOCKED: safe concurrent polling.
        # ORDER BY priority DESC first (migration 063): user-waited scan work
        # (run_llm_tests=200) always outranks a background sweep (=50) sitting
        # in the queue, then created_at keeps FIFO within a band. Backed by the
        # partial index idx_jobs_poll.
        job = db.execute(
            text(f"""
                SELECT id FROM jobs
                WHERE status = 'pending'{type_sql}
                ORDER BY priority DESC, created_at
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """),
            params,
        ).fetchone()

        if not job:
            return False

        job_id = job[0]
        job_obj = db.query(Job).filter(Job.id == job_id).first()
        if not job_obj:
            return False

        # Record the in-flight job BEFORE the running-commit so the graceful
        # shutdown handler (T5) can re-pend it on SIGTERM. Set even before the
        # commit: if SIGTERM fires in that window the job is still 'pending' and
        # the handler's `WHERE status='running'` guard makes the re-pend a no-op.
        _current_job_id = job_id

        job_obj.status = "running"
        job_obj.started_at = datetime.utcnow()
        job_obj.attempts = (job_obj.attempts or 0) + 1
        db.commit()

        handler = HANDLERS.get(job_obj.job_type)
        if not handler:
            job_obj.status = "failed"
            job_obj.result = {"error": f"Unknown job type: {job_obj.job_type}"}
            job_obj.completed_at = datetime.utcnow()
            db.commit()
            logger.error(f"Unknown job type: {job_obj.job_type}")
            return True

        logger.info(f"Executing job {job_obj.id} type={job_obj.job_type} scan={job_obj.scan_id} client={job_obj.client_id}")

        try:
            result = handler(
                job_payload=job_obj.payload or {},
                scan_id=str(job_obj.scan_id) if job_obj.scan_id else None,
                db=db,
            )
            job_obj.status = "completed"
            job_obj.result = result or {}
            job_obj.completed_at = datetime.utcnow()
            db.commit()
            logger.info(f"Job {job_obj.id} completed: {result}")

            # End-of-chain status flip - `run_llm_tests` sets `scan.status` to
            # 'completed' the moment its own LLM work finishes, but a retry of
            # any downstream job (cleanup_brands, materialize_content_items, …)
            # routes through `retry_scan` which sets the scan back to
            # 'scanning'. Nothing then flips it back to 'completed' when those
            # retried jobs succeed → the UI sticks on the Scan step forever.
            # Fix: at the end of every successful job, if the scan is still
            # 'scanning' and there are no pending/running/failed jobs left,
            # promote it to 'completed' here.
            if job_obj.scan_id:
                try:
                    from models import Scan as _Scan, Job as _Job
                    scan_obj = db.query(_Scan).filter(_Scan.id == job_obj.scan_id).first()
                    if scan_obj and scan_obj.status == "scanning":
                        in_flight = db.query(_Job).filter(
                            _Job.scan_id == job_obj.scan_id,
                            _Job.status.in_(["pending", "running", "failed"]),
                        ).count()
                        if in_flight == 0:
                            scan_obj.status = "completed"
                            if not scan_obj.completed_at:
                                scan_obj.completed_at = datetime.utcnow()
                            scan_obj.updated_at = datetime.utcnow()
                            db.commit()
                            logger.info(
                                f"Scan {scan_obj.id} flipped to completed "
                                f"(end-of-chain after {job_obj.job_type})"
                            )
                except Exception:
                    logger.exception(
                        f"End-of-chain status flip failed for scan {job_obj.scan_id}"
                    )

        except Exception as e:
            db.rollback()
            logger.exception(f"Job {job_obj.id} failed: {e}")

            # PermanentScanError signals "retrying won't help" - typically a
            # data-availability issue on user input (e.g., HaloScan has no
            # ranking data for the domain). Skip the retry loop, fail fast,
            # and tell the UI to hide the retry button.
            from exceptions import PermanentScanError
            is_permanent = isinstance(e, PermanentScanError)

            # Re-fetch job after rollback
            job_obj = db.query(Job).filter(Job.id == job_id).first()
            if job_obj:
                if is_permanent or (job_obj.attempts or 0) >= (job_obj.max_attempts or 3):
                    user_msg = str(e) if is_permanent else _format_user_error(e)
                    job_obj.status = "failed"
                    job_obj.attempts = job_obj.max_attempts  # block any further retry
                    job_obj.result = {
                        "error": str(e),
                        "user_message": user_msg,
                        "permanent": is_permanent,
                    }

                    if job_obj.job_type in POST_SCAN_AUDIT_JOB_TYPES:
                        # Post-scan audit / analytics job (S4 Wikipedia, S5
                        # Princeton, S6 schemas, S7 competitors, MR.4 outcome,
                        # MR.2 suggest_media, Pilier 5 refresh). The parent
                        # scan reached its terminal state before this job ran ;
                        # marking it 'failed' would blank all sub-tab pages
                        # via the layout's status-guard redirect (incident
                        # 2026-05-28). Just record the job-level error.
                        pass

                    elif job_obj.job_type in CONTENT_ITEM_JOB_TYPES:
                        # Content-item job (one FAQ / article). The parent scan
                        # already completed; only this item failed. Don't cascade
                        # to scan.status='failed' and don't run the scan-wide
                        # refund - refund the per-item content_credit instead.
                        item_id = (job_obj.payload or {}).get("item_id")
                        if item_id:
                            try:
                                _refund_content_item_credit(
                                    job_obj.scan_id, item_id, db,
                                    job_type=job_obj.job_type,
                                )
                            except Exception:
                                logger.exception(
                                    f"Failed to refund content_credit for item {item_id}"
                                )
                        # Reset the item back to 'identified' so the user can fix
                        # the input and retry from the validation page.
                        try:
                            from models import ScanContentItem
                            item = db.query(ScanContentItem).filter(
                                ScanContentItem.id == item_id
                            ).first() if item_id else None
                            if item and item.status in ("generating", "identified"):
                                item.status = "identified"
                        except Exception:
                            logger.exception(f"Failed to reset content item {item_id}")
                    else:
                        # Scan-pipeline job - mark scan failed + refund all
                        # net-debited credits for the scan.
                        from models import Scan
                        from sqlalchemy.orm.attributes import flag_modified
                        scan = db.query(Scan).filter(Scan.id == job_obj.scan_id).first()
                        if scan:
                            scan.status = "failed"
                            scan.error_message = user_msg
                            scan.updated_at = datetime.utcnow()
                            if is_permanent:
                                summary = dict(scan.summary or {})
                                summary["retryable"] = False
                                scan.summary = summary
                                flag_modified(scan, "summary")

                        try:
                            _refund_scan_credits(job_obj.scan_id, db)
                        except Exception:
                            logger.exception(
                                f"Failed to refund credits for scan {job_obj.scan_id}"
                            )
                else:
                    job_obj.status = "pending"  # Retry

                job_obj.completed_at = datetime.utcnow()
                db.commit()

        return True

    finally:
        # No job in flight once we return - a SIGTERM while idle/polling should
        # just exit, not try to re-pend a stale id.
        _current_job_id = None
        db.close()


def wait_for_db():
    """Wait for PostgreSQL to be ready and tables to exist."""
    from sqlalchemy import text
    for attempt in range(30):
        try:
            db = SessionLocal()
            db.execute(text("SELECT 1 FROM jobs LIMIT 0"))
            db.close()
            return
        except Exception:
            logger.info(f"Waiting for database... (attempt {attempt + 1})")
            time.sleep(2)
    raise RuntimeError("Database not ready after 60s")


def main():
    logger.info(f"Worker {settings.worker_id} starting, poll interval={settings.poll_interval}s")
    # Graceful shutdown (T5): catch the deploy/`docker stop` SIGTERM and Ctrl-C
    # so the in-flight job is re-pended for a clean resume instead of orphaned
    # as a 'running' zombie. Registered in the main thread (signals are only
    # delivered there). Supersedes the KeyboardInterrupt path below.
    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)
    wait_for_db()
    load_handlers()
    logger.info(f"Registered handlers: {list(HANDLERS.keys())}")

    while True:
        try:
            ping_heartbeat()  # throttled to 5min; no-op if HEALTHCHECK_WORKER_URL unset
            cleanup_stuck_jobs()  # cheap no-op except every CLEANUP_INTERVAL_SECONDS
            enqueue_post_publish_measurements()  # Pilier 7 T+14 sweep, every 1h
            enqueue_media_publish_outcomes()     # Phase MR.4 #3 media outcome sweep
            enqueue_media_catalog_discovery()    # Phase MR.1 catalog refresh, every 24h
            sweep_due_rescans()                  # S15.4 part B auto-rescan, every 5 min
            had_job = poll_and_execute()
            if not had_job:
                time.sleep(settings.poll_interval)
        except KeyboardInterrupt:
            logger.info("Worker shutting down")
            break
        except Exception:
            logger.exception("Unexpected error in poll loop")
            time.sleep(5)


if __name__ == "__main__":
    main()
