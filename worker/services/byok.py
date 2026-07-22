"""BYOK - runtime resolution of org-supplied LLM API keys (beta, migration 060).

Resolution semantics (locked product decisions - do not soften):
- Org key present + active + under cap  -> use it (key_source='byok')
- No key configured (or no org)         -> platform keys (key_source='platform')
- Key configured but status='invalid'   -> raise ByokKeyInvalid
- Key configured + monthly cap reached  -> raise ByokCapExceeded

A configured-but-broken key NEVER silently falls back to platform keys: the
org asked for their account to be used; burning platform quota against their
intent would be wrong, and the spend would bypass their cap. Both exceptions
fail the job through the normal retry chain (same pattern as
services/llm_budget.BudgetExceeded, which stays in place as a second net
regardless of key source).

Cap window = calendar month UTC, SUM(cost_usd) over llm_usage_log rows with
key_source='byok' for the org's clients + provider. Cap-then-call: the job
that crosses the cap finishes (overshoot <= that job's own cost), the next
one is blocked.

Resolution runs ONCE per handler execute() (clients are built once, reused
across all questions). No cross-job caching: keys can be added/removed
between jobs and the worker is a sequential poll loop, so 2-3 cheap queries
per job is fine.

Paths that stay on platform keys by design (no client context):
discover_media_catalog / media_catalog_classify (cross-client aggregation),
services/domain_classifier without a client arg, services/fan_out_extractor,
worker/scripts/*, and the api-side chatbot (api/routers/agent.py - deferred;
if ever wired, its missing usage logging MUST land in the same change).
seo_llm-internal Gemini calls (content path) also stay platform-keyed in v1:
the submodule's gemini rotator is a module-level singleton built from
GEMINI_API_KEYS at first use, so per-job env patching would silently no-op.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from config import settings

logger = logging.getLogger(__name__)

# Providers with a real runtime client today. Mistral keys are storable
# api-side but dormant until a Mistral provider ships.
BYOK_RUNTIME_PROVIDERS = ("openai", "anthropic", "gemini")

# Auth-failure signatures across the 3 providers' SDK/raw error strings.
# Used to flip a BYOK key to status='invalid' when it breaks mid-job.
AUTH_ERROR_MARKERS = (
    "401",
    "invalid api key",
    "incorrect api key",
    "invalid x-api-key",
    "authentication_error",
    "authentication error",
    "unauthorized",
    "permission_denied",
    "permission denied",
    "api key not valid",
    "invalid_api_key",
    "api_key_invalid",
)


class ByokCapExceeded(Exception):
    """Org monthly cap reached for a provider - job must NOT spend."""

    def __init__(self, org_id: str, provider: str, mtd_cost: float, cap: float):
        self.org_id = org_id
        self.provider = provider
        self.mtd_cost = mtd_cost
        self.cap = cap
        super().__init__(
            f"BYOK monthly cap reached for {provider} (${mtd_cost:.2f} of ${cap:.2f} this month, "
            f"org {org_id}). Raise the cap in Settings > LLM API keys, or delete the key to "
            f"fall back to platform keys."
        )


class ByokKeyInvalid(Exception):
    """Org key is configured but marked invalid - job must NOT spend."""

    def __init__(self, org_id: str, provider: str, last_error: str | None):
        self.org_id = org_id
        self.provider = provider
        self.last_error = last_error
        super().__init__(
            f"BYOK {provider} key for org {org_id} is invalid ({last_error or 'auth error'}). "
            f"Replace or re-validate it in Settings > LLM API keys, or delete it to fall back "
            f"to platform keys."
        )


@dataclass
class OrgKey:
    api_key: str  # decrypted plaintext - NEVER log
    org_id: str
    provider: str
    monthly_cap_usd: float | None


def get_org_id_for_client(db: Session, client_id) -> str | None:
    """clients.organization_id (nullable for legacy rows)."""
    if not client_id:
        return None
    from models import Client
    row = db.query(Client.organization_id).filter(Client.id == client_id).first()
    return str(row[0]) if row and row[0] else None


def get_month_byok_cost(db: Session, org_id, provider: str) -> float:
    """Calendar-month-to-date BYOK spend for (org, provider).

    Counts ONLY key_source='byok' rows: the cap means "don't spend more than
    $X/month on MY provider account" - platform-key spend never counts.
    """
    from models import Client, LlmUsageLog
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    val = (
        db.query(func.coalesce(func.sum(LlmUsageLog.cost_usd), 0.0))
        .join(Client, Client.id == LlmUsageLog.client_id)
        .filter(
            Client.organization_id == org_id,
            LlmUsageLog.provider == provider,
            LlmUsageLog.key_source == "byok",
            LlmUsageLog.created_at >= month_start,
        )
        .scalar()
    )
    return float(val or 0.0)


def resolve_org_key(db: Session, client_id, provider: str) -> OrgKey | None:
    """Core resolution. None = use the platform path (soft fallback).

    Raises ByokKeyInvalid / ByokCapExceeded when a key IS configured but
    unusable - never returns a platform fallback in that case.
    """
    org_id = get_org_id_for_client(db, client_id)
    if not org_id:
        return None
    from models import OrganizationApiKey
    row = (
        db.query(OrganizationApiKey)
        .filter(
            OrganizationApiKey.organization_id == org_id,
            OrganizationApiKey.provider == provider,
        )
        .first()
    )
    if row is None:
        return None
    if row.status == "invalid":
        raise ByokKeyInvalid(org_id, provider, row.last_error)
    cap = float(row.monthly_cap_usd) if row.monthly_cap_usd is not None else None
    if cap is not None:
        mtd = get_month_byok_cost(db, org_id, provider)
        if mtd >= cap:
            raise ByokCapExceeded(org_id, provider, mtd, cap)
    from adapters.token_manager import decrypt_token
    api_key = decrypt_token(row.api_key_encrypted)
    logger.info("BYOK: using org key for provider=%s org=%s client=%s", provider, org_id, client_id)
    return OrgKey(api_key=api_key, org_id=org_id, provider=provider, monthly_cap_usd=cap)


def resolve_openai_key(db: Session, client_id) -> tuple[str, str]:
    """(api_key, key_source). Platform fallback = settings.openai_api_key."""
    org_key = resolve_org_key(db, client_id, "openai")
    if org_key is not None:
        return org_key.api_key, "byok"
    return settings.openai_api_key, "platform"


def resolve_anthropic_key(db: Session, client_id) -> tuple[str, str]:
    """(api_key, key_source). Platform fallback = settings.anthropic_api_key."""
    org_key = resolve_org_key(db, client_id, "anthropic")
    if org_key is not None:
        return org_key.api_key, "byok"
    return settings.anthropic_api_key, "platform"


def resolve_ytg_key(db: Session, client_id) -> tuple[str | None, str]:
    """(api_key, key_source) for YourTextGuru (SEO tool, content pipeline).

    Platform fallback returns None: the seo_llm YTGClient reads
    YOURTEXTGURU_API_KEY from env itself, so callers only patch the env when
    key_source == 'byok'. Raises ByokKeyInvalid if the org key is marked
    invalid (no silent platform fallback, same doctrine as the LLM keys). SEO
    keys carry no monthly_cap_usd, so ByokCapExceeded never fires unless an org
    sets a cap explicitly.
    """
    org_key = resolve_org_key(db, client_id, "yourtextguru")
    if org_key is not None:
        return org_key.api_key, "byok"
    return None, "platform"


def resolve_babbar_key(db: Session, client_id) -> tuple[str | None, str]:
    """(api_key, key_source) for Babbar (SEO tool). Platform fallback = None
    (BabbarClient reads BABBAR_API_KEY from env itself). See resolve_ytg_key."""
    org_key = resolve_org_key(db, client_id, "babbar")
    if org_key is not None:
        return org_key.api_key, "byok"
    return None, "platform"


def make_gemini_client(db: Session, client_id, model: str | None = None):
    """(client, key_source). Duck-type-identical either way:

    - byok     -> single-key seo_llm LLMClient (their key, their quota - no
                  pool rotation; a 429 fails/retries the job like any provider
                  error)
    - platform -> PoolRotatingGeminiClient over the shared platform pool
                  (wrapped as-is, per-call rotation + park-and-retry preserved)

    Returns (None, 'platform') when no org key AND the platform pool is empty
    (callers keep their existing skip behavior).
    """
    org_key = resolve_org_key(db, client_id, "gemini")
    if org_key is not None:
        from adapters.llm_scanner import create_llm_client
        client = (create_llm_client("gemini", org_key.api_key, model=model)
                  if model else create_llm_client("gemini", org_key.api_key))
        return client, "byok"
    from services.gemini_key_pool import get_gemini_pool
    pool = get_gemini_pool()
    if not pool.has_keys():
        return None, "platform"
    # Lazy import - handlers.run_llm_tests imports this module at module level,
    # so importing it back at module level here would be a cycle.
    from handlers.run_llm_tests import PoolRotatingGeminiClient
    return PoolRotatingGeminiClient(pool, model=model), "platform"


def is_auth_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(m in msg for m in AUTH_ERROR_MARKERS)


def mark_org_key_invalid(db: Session, org_id, provider: str, error_msg: str) -> None:
    """Flip the org key to status='invalid' so the next job raises
    ByokKeyInvalid immediately and the UI shows the actionable error.
    Best-effort: never masks the original provider error at the call site.
    """
    if not org_id:
        return
    try:
        from models import OrganizationApiKey
        row = (
            db.query(OrganizationApiKey)
            .filter(
                OrganizationApiKey.organization_id == org_id,
                OrganizationApiKey.provider == provider,
            )
            .first()
        )
        if row is None:
            return
        row.status = "invalid"
        row.last_error = (error_msg or "auth error")[:500]
        row.updated_at = datetime.utcnow()
        db.commit()
        logger.warning("BYOK: marked %s key invalid for org %s (auth error at runtime)", provider, org_id)
    except Exception:
        logger.exception("BYOK: failed to mark key invalid (org=%s provider=%s)", org_id, provider)


@contextmanager
def patched_llm_env(openai_key: str | None = None, anthropic_key: str | None = None,
                    ytg_key: str | None = None, babbar_key: str | None = None):
    """Temporarily override OPENAI_API_KEY / ANTHROPIC_API_KEY (LLM) and
    YOURTEXTGURU_API_KEY / BABBAR_API_KEY (SEO tools) in os.environ for the
    seo_llm content path (the submodule's clients read keys via os.getenv at
    generator-instantiation time; we never edit the submodule). The generator
    is built INSIDE this window, so each client picks up the org key.

    GEMINI_API_KEYS is deliberately NOT patched - the submodule's gemini
    rotator (seo_llm/src/gemini_key_rotator.py) is a module-level singleton
    built once from env at first use: patching after job 1 would silently
    no-op, and resetting its private global reaches into submodule internals.
    Content-path Gemini stays platform-keyed in v1 (logged key_source=
    'platform', so it never counts toward the org's gemini cap - correct).

    SAFE ONLY because each worker container is a single-threaded sequential
    poll loop (one job at a time). If jobs ever run concurrently in one
    process, this MUST be replaced by explicit key injection.
    """
    patch = {}
    if openai_key:
        patch["OPENAI_API_KEY"] = openai_key
    if anthropic_key:
        patch["ANTHROPIC_API_KEY"] = anthropic_key
    if ytg_key:
        patch["YOURTEXTGURU_API_KEY"] = ytg_key
    if babbar_key:
        patch["BABBAR_API_KEY"] = babbar_key
    saved = {k: os.environ.get(k) for k in patch}
    try:
        os.environ.update(patch)
        yield
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old
