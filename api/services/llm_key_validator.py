"""BYOK - validate an org-supplied API key with one authenticated ping.

LLM providers expose a free models-list endpoint, so their validation never
spends tokens. SEO-tool providers (YourTextGuru, Babbar) have no free
models-list, so they are validated with one small authenticated call to a
cheap endpoint (still a single request; cf. feedback_cap_user_triggered_llm_ops).
Called by the PUT /organizations/{id}/api-keys/{provider} endpoint before
anything is stored, and by POST .../validate for recovery.

Security invariants :
- The plaintext key is NEVER logged. Log lines carry provider + HTTP status only.
- Error messages returned to the UI are actionable but key-free.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

# LLM providers - validated with a free models-list ping (zero token cost).
BYOK_LLM_PROVIDERS = ("openai", "anthropic", "gemini", "mistral")
# External SEO-tool providers (content pipeline). NOT LLMs: validated with a
# real authenticated call (no free models-list), and they carry no
# llm_usage_log $ spend, so the monthly_cap_usd machinery does not apply.
# 'haloscan' is DB-whitelisted (migration 065) but not exposed for entry until
# its runtime wiring ships (Phase 3), so it stays out of this allowlist.
BYOK_SEO_PROVIDERS = ("yourtextguru", "babbar")
BYOK_PROVIDERS = BYOK_LLM_PROVIDERS + BYOK_SEO_PROVIDERS

_PROVIDER_LABELS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Google Gemini",
    "mistral": "Mistral",
    "yourtextguru": "YourTextGuru",
    "babbar": "Babbar",
}

_TIMEOUT = 10.0


def _ping_request(provider: str, api_key: str) -> tuple[str, str, dict, dict, dict | None]:
    """Return (method, url, headers, params, json_body) for a cheap authenticated
    validation call. LLM providers use a free models-list GET; SEO tools use a
    real authenticated endpoint (they have no free models-list)."""
    if provider == "openai":
        return ("GET", "https://api.openai.com/v1/models",
                {"Authorization": f"Bearer {api_key}"}, {}, None)
    if provider == "anthropic":
        return ("GET", "https://api.anthropic.com/v1/models",
                {"x-api-key": api_key, "anthropic-version": "2023-06-01"}, {}, None)
    if provider == "gemini":
        return ("GET", "https://generativelanguage.googleapis.com/v1beta/models",
                {}, {"key": api_key}, None)
    if provider == "mistral":
        return ("GET", "https://api.mistral.ai/v1/models",
                {"Authorization": f"Bearer {api_key}"}, {}, None)
    if provider == "yourtextguru":
        # GET /status returns the account + remaining tokens (Bearer auth).
        return ("GET", "https://yourtext.guru/api/v2/status",
                {"Authorization": f"Bearer {api_key}"}, {}, None)
    if provider == "babbar":
        # Cheapest authenticated endpoint; api_token as query param. A bad token
        # returns 401/403; runtime mark_org_key_invalid is the backstop.
        return ("POST", "https://www.babbar.tech/api/host/overview/main",
                {}, {"api_token": api_key}, {"host": "babbar.tech"})
    raise ValueError(f"Unsupported BYOK provider: {provider}")


async def validate_llm_key(provider: str, api_key: str) -> tuple[bool, str | None]:
    """One free models-list call. Returns (ok, error_message).

    error_message is user-facing (English, actionable, no em-dash, never
    contains the key).
    """
    label = _PROVIDER_LABELS.get(provider, provider)
    method, url, headers, params, json_body = _ping_request(provider, api_key)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.request(method, url, headers=headers, params=params, json=json_body)
    except (httpx.TimeoutException, httpx.ConnectError):
        logger.warning("BYOK key validation: %s unreachable (network)", provider)
        return False, f"{label} could not be reached. Try again in a minute."
    except httpx.HTTPError as e:
        logger.warning("BYOK key validation: %s transport error %s", provider, type(e).__name__)
        return False, f"{label} request failed. Try again in a minute."

    status = resp.status_code
    logger.info("BYOK key validation: provider=%s status=%s", provider, status)
    if status == 200:
        return True, None
    if status in (401, 403):
        # Gemini signals a bad key with 400 API_KEY_INVALID, handled below.
        if status == 403:
            return False, (
                f"Key rejected by {label} (missing permissions). "
                "Use a key that can list models and call the API."
            )
        return False, f"Key rejected by {label} (invalid or revoked). Check it in your {label} console."
    if status == 400 and provider == "gemini":
        return False, "Key rejected by Google Gemini (invalid or revoked). Check it in Google AI Studio."
    if provider in BYOK_SEO_PROVIDERS:
        # SEO-tool endpoints lack the clean models-list 200/40x contract of the
        # LLM APIs. 401/403 above already reject a clearly-bad key; for any
        # other non-200 we store optimistically rather than falsely reject a
        # valid key (a truly dead key is flagged at runtime by
        # byok.mark_org_key_invalid).
        logger.info("BYOK: %s key stored without strict validation (HTTP %s, tolerant)", provider, status)
        return True, None
    if status == 429:
        return False, f"{label} is rate limiting requests right now. Try again in a minute."
    return False, f"{label} returned an unexpected error (HTTP {status}). Try again, or check the key in your provider console."


def make_key_hint(api_key: str) -> str:
    """Masked display form, e.g. 'sk-pr...abc4'. Never store/show more."""
    if len(api_key) < 12:
        return "..." + api_key[-2:]
    return f"{api_key[:5]}...{api_key[-4:]}"
