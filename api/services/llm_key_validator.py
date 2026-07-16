"""BYOK - validate an org-supplied LLM API key with one zero-cost ping.

Every supported provider exposes a free models-list endpoint, so validation
never spends tokens. Called by the PUT /organizations/{id}/api-keys/{provider}
endpoint before anything is stored, and by POST .../validate for recovery.

Security invariants :
- The plaintext key is NEVER logged. Log lines carry provider + HTTP status only.
- Error messages returned to the UI are actionable but key-free.
"""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

BYOK_PROVIDERS = ("openai", "anthropic", "gemini", "mistral")

_PROVIDER_LABELS = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Google Gemini",
    "mistral": "Mistral",
}

_TIMEOUT = 10.0


def _ping_request(provider: str, api_key: str) -> tuple[str, dict, dict]:
    """Return (url, headers, params) for the provider's free models-list ping."""
    if provider == "openai":
        return ("https://api.openai.com/v1/models",
                {"Authorization": f"Bearer {api_key}"}, {})
    if provider == "anthropic":
        return ("https://api.anthropic.com/v1/models",
                {"x-api-key": api_key, "anthropic-version": "2023-06-01"}, {})
    if provider == "gemini":
        return ("https://generativelanguage.googleapis.com/v1beta/models",
                {}, {"key": api_key})
    if provider == "mistral":
        return ("https://api.mistral.ai/v1/models",
                {"Authorization": f"Bearer {api_key}"}, {})
    raise ValueError(f"Unsupported BYOK provider: {provider}")


async def validate_llm_key(provider: str, api_key: str) -> tuple[bool, str | None]:
    """One free models-list call. Returns (ok, error_message).

    error_message is user-facing (English, actionable, no em-dash, never
    contains the key).
    """
    label = _PROVIDER_LABELS.get(provider, provider)
    url, headers, params = _ping_request(provider, api_key)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(url, headers=headers, params=params)
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
    if status == 429:
        return False, f"{label} is rate limiting requests right now. Try again in a minute."
    return False, f"{label} returned an unexpected error (HTTP {status}). Try again, or check the key in your provider console."


def make_key_hint(api_key: str) -> str:
    """Masked display form, e.g. 'sk-pr...abc4'. Never store/show more."""
    if len(api_key) < 12:
        return "..." + api_key[-2:]
    return f"{api_key[:5]}...{api_key[-4:]}"
