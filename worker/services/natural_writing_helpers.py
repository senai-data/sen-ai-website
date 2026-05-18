"""Worker-side wrappers around shared/natural_writing/ for ergonomic injection
into existing LLM-handler prompts.

Most handlers build their prompt as a single string via .format() then send it
to Gemini / Claude. This module exposes a single helper that appends the
humanizer prompt section to any such string, with the right mode budget.

Usage in a handler :

    from services.natural_writing_helpers import inject_humanizer

    prompt = MY_PROMPT_TEMPLATE.format(brand=brand_name, ...)
    prompt = inject_humanizer(prompt, mode='compact')
    response = llm_client.generate(prompt)

The helper degrades gracefully : if the shared package fails to import (e.g.
volume not mounted), it logs a warning and returns the prompt unchanged
rather than killing the handler.
"""

from __future__ import annotations
import logging
import sys

logger = logging.getLogger(__name__)

# Ensure /app is on sys.path so `from shared...` works when the helper is
# imported by a handler that runs from /app/handlers/. Idempotent.
if "/app" not in sys.path:
    sys.path.insert(0, "/app")


def inject_humanizer(prompt: str, mode: str = "compact", language: str = "fr") -> str:
    """Append the natural-writing humanizer prompt section to `prompt`.

    Args:
        prompt: the existing handler prompt (system message or single-shot).
        mode: 'full' (article, newsletter) | 'compact' (FAQ, briefs, personas) |
            'chat' (chatbot) | 'tooltip' (offline audit). See shared/natural_writing/modes.py.
        language: 'fr' (default) or 'en'. Sources are English-flavoured but the
            rule block is bilingual.

    Returns the prompt with the humanizer section appended. Falls back to the
    original prompt unchanged on import / fetch failure.
    """
    if not prompt:
        return prompt
    try:
        from shared.natural_writing import get_prompt_section
        section = get_prompt_section(mode=mode, language=language)
        if not section:
            return prompt
        # Use a clear separator so the LLM treats it as a distinct directive
        # block, not a continuation of the upstream prompt.
        return prompt.rstrip() + "\n\n" + section
    except Exception:
        logger.exception(
            "inject_humanizer failed for mode=%s - returning prompt unchanged",
            mode,
        )
        return prompt


def sanitize_output(html: str, mode: str = "compact", brand_content: str = "") -> str:
    """Run the natural-writing sanitizer gates for the given mode on LLM output.

    Args:
        html: LLM-generated HTML to clean.
        mode: same mode keys as inject_humanizer.
        brand_content: optional scraped brand site text - used by the
            fake_experts gate to whitelist known names.

    Returns the cleaned HTML. Never raises ; each gate handles its own
    exceptions and returns its input unchanged on failure.
    """
    if not html:
        return html
    try:
        from shared.natural_writing import sanitize, get_mode
        gates = get_mode(mode).sanitizer_gates
        return sanitize(html, gates=gates, brand_content=brand_content)
    except Exception:
        logger.exception(
            "sanitize_output failed for mode=%s - returning html unchanged", mode,
        )
        return html
