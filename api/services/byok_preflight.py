"""BYOK - api-side pre-flight for scan launch/rescan (beta, migration 060).

Launch debits scan credits BEFORE the worker job runs, so launching into a
deterministic BYOK failure would burn credits for nothing. This module blocks
the launch (HTTPException, same pattern as services/access.py) when a
configured org key makes the scan certain to fail worker-side:

- key configured + status='invalid'  -> 400 byok_key_invalid
- key configured + monthly cap hit   -> 402 byok_cap_exceeded
- no key configured                  -> platform path, no block

The worker-side check (worker/services/byok.py, resolve_org_key) remains the
authoritative second net - the cap can be crossed between launch and
execution by another job.

Providers checked = the scan's selected providers UNION {'gemini'}: the
EntityAnalyzer runs on gemini for every scan, and the worker blocks on a
capped/invalid gemini org key even when only openai is selected (a scan
without brand analysis produces false 0% metrics). Anthropic never blocks a
launch (used by post-scan jobs whose failure doesn't cascade) but is included
in the recorded byok_providers list for the compliance report.
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func

from models import Client, LlmUsageLog, OrganizationApiKey

logger = logging.getLogger(__name__)

BYOK_RUNTIME_PROVIDERS = ("openai", "anthropic", "gemini")


def _org_id_for_client(db, client_id) -> str | None:
    row = db.query(Client.organization_id).filter(Client.id == client_id).first()
    return str(row[0]) if row and row[0] else None


def _month_byok_cost(db, org_id, provider: str) -> float:
    """Calendar-month-to-date spend on the org's own key (key_source='byok')."""
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


def _org_keys(db, org_id) -> dict[str, OrganizationApiKey]:
    rows = (
        db.query(OrganizationApiKey)
        .filter(OrganizationApiKey.organization_id == org_id)
        .all()
    )
    return {r.provider: r for r in rows}


def is_byok_complete(db, org_id) -> bool:
    """Pricing eligibility ('BYOK complete') : active keys for the 3 runtime
    providers (openai + gemini + anthropic). Mistral excluded - no runtime."""
    if not org_id:
        return False
    keys = _org_keys(db, org_id)
    return all(
        p in keys and keys[p].status == "active"
        for p in BYOK_RUNTIME_PROVIDERS
    )


def preflight_scan_launch(db, client_id, providers: list[str]) -> list[str]:
    """Block (400/402) or return the byok_providers list to record on
    scan.config (empty list = fully platform-keyed scan).

    The returned list covers ALL runtime providers with an active org key
    (openai/anthropic/gemini), not just the selected ones - it feeds the
    per-scan compliance report ("which keys carried this scan's prompts").
    """
    org_id = _org_id_for_client(db, client_id)
    if not org_id:
        return []
    keys = _org_keys(db, org_id)
    if not keys:
        return []

    # The analyzer always needs gemini; selected providers need themselves.
    blocking = set(p for p in providers if p in BYOK_RUNTIME_PROVIDERS)
    blocking.add("gemini")

    for provider in sorted(blocking):
        row = keys.get(provider)
        if row is None:
            continue  # no key = platform path, never blocks
        if row.status == "invalid":
            raise HTTPException(400, {
                "error": "byok_key_invalid",
                "provider": provider,
                "last_error": row.last_error,
                "message": (
                    f"Your {provider} API key was rejected by the provider. "
                    f"Replace, re-validate or delete it in Settings > LLM API keys."
                ),
            })
        cap = float(row.monthly_cap_usd) if row.monthly_cap_usd is not None else None
        if cap is not None:
            mtd = _month_byok_cost(db, org_id, provider)
            if mtd >= cap:
                raise HTTPException(402, {
                    "error": "byok_cap_exceeded",
                    "provider": provider,
                    "mtd_cost_usd": round(mtd, 2),
                    "monthly_cap_usd": cap,
                    "message": (
                        f"Your {provider} API key hit its monthly cap "
                        f"(${mtd:.2f} of ${cap:.2f}). Raise the cap in Settings > "
                        f"LLM API keys, or delete the key to use platform keys."
                    ),
                })

    return [
        p for p in BYOK_RUNTIME_PROVIDERS
        if p in keys and keys[p].status == "active"
    ]
