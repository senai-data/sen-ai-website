"""Per-scan model-version selector allowlist (BYOK-gated feature).

PARITE obligatoire avec api/services/model_allowlist.py - the api validates
user input against it, the worker re-validates at execution time (safety belt
against api/worker deploy drift : unknown model falls back to the platform
default with a warning, it never runs an arbitrary string).

Doctrine (decided 2026-07-16 with the user) :
- anchored on what CONSUMERS see (the product measures consumer AI answers),
  with a cheaper-but-close option ; short list, only models that matter.
- first entry per provider = the platform default (a scan without override
  must behave byte-identically to the pre-selector behavior).
- NO moving aliases (chat-latest) : an alias changes behavior without
  changing its string, which P3 model eras cannot annotate.
- the EntityAnalyzer model is the measurement instrument and is NEVER
  user-selectable.
- only openai + gemini : the only providers with a live scan runtime.
"""

SCAN_MODEL_ALLOWLIST: dict[str, list[dict]] = {
    "openai": [
        {
            "model": "gpt-4.1-mini",
            "label": "GPT-4.1 mini",
            "tag": "default - most economical",
            "note": "Cost-efficient - platform default",
            "price": "$0.40 / $1.60 per 1M tokens",
        },
        {
            "model": "gpt-5.5",
            "label": "GPT-5.5",
            "tag": "closest to consumer ChatGPT",
            "note": "Closest to consumer ChatGPT (free tier)",
            "price": "$5 / $30 per 1M tokens",
            "recommended": True,
        },
        {
            "model": "gpt-5.6-luna",
            "label": "GPT-5.6 Luna",
            "tag": "budget tier of the flagship family",
            "note": "Budget tier of the current flagship family",
            "price": "$1 / $6 per 1M tokens",
        },
    ],
    "gemini": [
        {
            "model": "gemini-3.5-flash",
            "label": "Gemini 3.5 Flash",
            "tag": "default - matches consumer Gemini & AI Mode",
            "note": "Matches the consumer Gemini app and AI Mode - platform default",
            "price": "$1.50 / $9 per 1M tokens",
        },
    ],
}


def allowed_models(provider: str) -> list[str]:
    return [e["model"] for e in SCAN_MODEL_ALLOWLIST.get(provider, [])]


def default_model(provider: str) -> str | None:
    entries = SCAN_MODEL_ALLOWLIST.get(provider) or []
    return entries[0]["model"] if entries else None


def is_allowed(provider: str, model: str) -> bool:
    return model in allowed_models(provider)
